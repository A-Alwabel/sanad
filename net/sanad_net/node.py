"""Sanad node v0.3 — the polite node, cross-platform.

Wraps a ggml-rpc-server process and registers it with the coordinator, with
"good guest" behavior baked in:

- **Memory pledge** (--pledge-mb): the operator declares how much RAM to lend.
  The coordinator enforces the matching layer share via --tensor-split, and
  credits are earned in proportion to it.
- **Low OS priority**: the rpc-server runs below normal priority, so anything
  the owner opens (a game, a browser, work) takes the CPU first — enforced by
  the OS itself, not by our goodwill.
- **Busy sensor** (--busy-at / --resume-at): samples CPU load from processes
  OTHER than our own rpc-server. Sustained high load means the owner needs the
  machine: the node drains out gracefully (/leave + rpc-server stopped, all
  memory returned). When the machine has been calm for a while, it rejoins and
  re-registers automatically. Withdrawal is never punished: credits are kept.
  Set --busy-at 101 for a dedicated (always-on) node.

The sensor measures load from processes other than this node's own rpc-server.
On a machine that also runs the coordinator's engine (i.e. a single-machine
test), that engine's inference load counts as "the owner" and will drain the
node out — correct behavior from the sensor's point of view, but an artifact of
co-hosting. Raise --busy-at above ordinary inference load in that setup; on
separate machines the engine is not a neighbour at all.

Sensing is native per platform and never shells out to a subprocess — an early
version used PowerShell and starved under exactly the load it was meant to
detect:
  Windows  kernel32 GetSystemTimes / GetProcessTimes (exact, per-process)
  Linux    /proc/stat + /proc/<pid>/stat             (exact, per-process)
  other    os.getloadavg() / cpu_count               (coarse, whole-machine)

Usage:
    python -m sanad_net.node --node-id riyadh-a --operator amina \
        --port 50070 --pledge-mb 1000 --coordinator http://192.168.0.10:7860 \
        --rpc-bin ../.local/bin --host 0.0.0.0
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

EXE = ".exe" if os.name == "nt" else ""
HEARTBEAT_S = 3.0
SENSOR_PERIOD_S = 2.0
BUSY_SAMPLES = 2      # consecutive busy samples before draining out
CALM_SAMPLES = 3      # consecutive calm samples before rejoining


def post(url: str, payload: dict, timeout: float = 10) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


# --------------------------------------------------------------------------
# CPU sampling — one class per platform, all subprocess-free
# --------------------------------------------------------------------------

class _WindowsSampler:
    _QUERY = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION

    def __init__(self) -> None:
        import ctypes
        self._ct = ctypes
        self._k32 = ctypes.windll.kernel32
        self._prev_sys: tuple[int, int, int] | None = None
        self._prev_proc: tuple[int, float] | None = None
        self._cores = os.cpu_count() or 1

    def _fts(self, n: int):
        class FILETIME(self._ct.Structure):
            _fields_ = [("lo", self._ct.c_uint32), ("hi", self._ct.c_uint32)]
        return [FILETIME() for _ in range(n)]

    @staticmethod
    def _v(ft) -> int:
        return (ft.hi << 32) | ft.lo

    def _sys(self):
        idle, kern, user = self._fts(3)
        if not self._k32.GetSystemTimes(self._ct.byref(idle), self._ct.byref(kern),
                                        self._ct.byref(user)):
            return None
        return self._v(idle), self._v(kern), self._v(user)

    def _proc(self, pid: int):
        h = self._k32.OpenProcess(self._QUERY, False, pid)
        if not h:
            return None
        try:
            created, exited, kern, user = self._fts(4)
            if not self._k32.GetProcessTimes(h, self._ct.byref(created), self._ct.byref(exited),
                                             self._ct.byref(kern), self._ct.byref(user)):
                return None
            return self._v(kern) + self._v(user)
        finally:
            self._k32.CloseHandle(h)

    def other_load_percent(self, own_pid: int | None) -> float | None:
        try:
            s_now = self._sys()
            p_now = self._proc(own_pid) if own_pid else 0
            now = time.time()
        except Exception:
            s_now = p_now = None
            now = time.time()
        if s_now is None or p_now is None:
            self._prev_sys = self._prev_proc = None
            return None
        out = None
        if self._prev_sys is not None and self._prev_proc is not None:
            d_idle = s_now[0] - self._prev_sys[0]
            d_total = (s_now[1] - self._prev_sys[1]) + (s_now[2] - self._prev_sys[2])
            d_wall = max(now - self._prev_proc[1], 0.25)
            if d_total > 0:                     # kernel time already includes idle
                total_pct = (1.0 - d_idle / d_total) * 100.0
                own_pct = ((p_now - self._prev_proc[0]) / 1e7 / d_wall / self._cores) * 100.0
                out = max(total_pct - own_pct, 0.0)
        self._prev_sys = s_now
        self._prev_proc = (p_now, now)
        return out


class _LinuxSampler:
    def __init__(self) -> None:
        self._prev_sys: tuple[int, int] | None = None   # (idle, total)
        self._prev_proc: tuple[float, float] | None = None
        self._cores = os.cpu_count() or 1
        self._hz = float(os.sysconf("SC_CLK_TCK")) if hasattr(os, "sysconf") else 100.0

    def _sys(self):
        with open("/proc/stat", "r") as fh:
            parts = fh.readline().split()
        vals = [int(v) for v in parts[1:11]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)   # idle + iowait
        return idle, sum(vals)

    def _proc(self, pid: int) -> float | None:
        try:
            with open(f"/proc/{pid}/stat", "r") as fh:
                fields = fh.read().rsplit(")", 1)[1].split()
            return (int(fields[11]) + int(fields[12])) / self._hz   # utime + stime
        except (OSError, IndexError, ValueError):
            return None

    def other_load_percent(self, own_pid: int | None) -> float | None:
        try:
            s_now = self._sys()
            p_now = self._proc(own_pid) if own_pid else 0.0
            now = time.time()
        except Exception:
            self._prev_sys = self._prev_proc = None
            return None
        if p_now is None:
            self._prev_sys = self._prev_proc = None
            return None
        out = None
        if self._prev_sys is not None and self._prev_proc is not None:
            d_idle = s_now[0] - self._prev_sys[0]
            d_total = s_now[1] - self._prev_sys[1]
            d_wall = max(now - self._prev_proc[1], 0.25)
            if d_total > 0:
                total_pct = (1.0 - d_idle / d_total) * 100.0
                own_pct = ((p_now - self._prev_proc[0]) / d_wall / self._cores) * 100.0
                out = max(total_pct - own_pct, 0.0)
        self._prev_sys = s_now
        self._prev_proc = (p_now, now)
        return out


class _LoadAvgSampler:
    """Fallback for macOS/BSD: 1-minute load average as a whole-machine signal.

    Coarse — it cannot subtract our own usage and it lags by design — so the
    node is more conservative here: it reads a busy machine late rather than
    hallucinating one. Contributions of a native darwin sampler are welcome.
    """

    def __init__(self) -> None:
        self._cores = os.cpu_count() or 1

    def other_load_percent(self, own_pid: int | None) -> float | None:
        try:
            one_min = os.getloadavg()[0]
        except (OSError, AttributeError):
            return None
        return max(min(one_min / self._cores * 100.0, 100.0), 0.0)


def make_sampler():
    if os.name == "nt":
        return _WindowsSampler()
    if sys.platform.startswith("linux") and os.path.exists("/proc/stat"):
        return _LinuxSampler()
    return _LoadAvgSampler()


class SensorFSM:
    """Pure politeness state machine. step() returns 'drain', 'rejoin', or None.

    A `load` of None means "sample failed": both streaks reset and no action is
    taken — the node never changes state on missing information.
    """

    def __init__(self, busy_at: float, resume_at: float,
                 busy_samples: int = BUSY_SAMPLES, calm_samples: int = CALM_SAMPLES) -> None:
        self.busy_at = busy_at
        self.resume_at = resume_at
        self.busy_samples = busy_samples
        self.calm_samples = calm_samples
        self.busy_streak = 0
        self.calm_streak = 0

    def step(self, load: float | None, serving: bool) -> str | None:
        if load is None:
            self.busy_streak = self.calm_streak = 0
            return None
        if serving:
            self.busy_streak = self.busy_streak + 1 if load >= self.busy_at else 0
            if self.busy_streak >= self.busy_samples:
                self.busy_streak = 0
                return "drain"
        else:
            self.calm_streak = self.calm_streak + 1 if load <= self.resume_at else 0
            if self.calm_streak >= self.calm_samples:
                self.calm_streak = 0
                return "rejoin"
        return None


def low_priority_kwargs() -> dict:
    """Spawn options that put the owner's own apps ahead of us."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)}
    return {"preexec_fn": lambda: os.nice(10)}   # POSIX: 10 niceness levels back


class PoliteNode:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.bin_dir = Path(args.rpc_bin).resolve()
        self.rpc_exe = self.bin_dir / f"ggml-rpc-server{EXE}"
        if not self.rpc_exe.exists():
            sys.exit(f"ggml-rpc-server{EXE} not found in {self.bin_dir}")
        self.proc: subprocess.Popen | None = None
        self.serving = False
        self.fsm = SensorFSM(args.busy_at, args.resume_at)
        self.sampler = make_sampler() if args.busy_at <= 100 else None
        # The address the coordinator should dial. 0.0.0.0 means "listen
        # everywhere", which is not a dialable address — advertise the real one.
        self.advertise = args.advertise or (
            self._lan_ip() if args.host in ("0.0.0.0", "::") else args.host
        )

    @staticmethod
    def _lan_ip() -> str:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))          # no packet is sent; picks the default route
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            s.close()

    def log(self, msg: str) -> None:
        print(f"[{self.args.node_id}] {msg}", flush=True)

    # -- rpc-server lifecycle ------------------------------------------------
    def start_serving(self) -> bool:
        self.proc = subprocess.Popen(
            [str(self.rpc_exe), "-H", self.args.host, "-p", str(self.args.port),
             "-t", str(self.args.threads)],
            cwd=str(self.bin_dir),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **low_priority_kwargs(),
        )
        payload = {
            "node_id": self.args.node_id, "host": self.advertise,
            "port": self.args.port, "operator": self.args.operator,
            "pledge_mb": self.args.pledge_mb,
        }
        for _ in range(10):
            try:
                post(f"{self.args.coordinator}/register", payload)
                self.serving = True
                self.log(f"SERVING on {self.advertise}:{self.args.port} "
                         f"(pledge {self.args.pledge_mb:g} MB, low OS priority, "
                         f"operator {self.args.operator})")
                return True
            except Exception:
                time.sleep(1.0)
        self.stop_serving(polite=False)
        return False

    def stop_serving(self, polite: bool = True) -> None:
        if polite:
            try:
                post(f"{self.args.coordinator}/leave", {"node_id": self.args.node_id})
            except Exception:
                pass
        if self.proc is not None:
            self.proc.terminate()
            self.proc = None
        self.serving = False

    # -- main loop -------------------------------------------------------------
    def run(self) -> None:
        if not self.start_serving():
            sys.exit(f"could not reach coordinator at {self.args.coordinator}")
        last_beat = 0.0
        try:
            while True:
                if self.serving and self.proc is not None and self.proc.poll() is not None:
                    self.log("rpc-server exited unexpectedly; leaving network")
                    self.stop_serving()
                if self.serving and time.time() - last_beat >= HEARTBEAT_S:
                    try:
                        post(f"{self.args.coordinator}/heartbeat", {"node_id": self.args.node_id})
                    except Exception:
                        pass
                    last_beat = time.time()

                if self.sampler is None:            # dedicated node: sensor off
                    time.sleep(SENSOR_PERIOD_S)
                    continue

                try:
                    load = self.sampler.other_load_percent(self.proc.pid if self.proc else None)
                except Exception:
                    load = None                     # a sensor error must never crash the node
                action = self.fsm.step(load, self.serving)
                if action == "drain":
                    self.log(f"owner is busy (other-process load ~{load:.0f}%) -> "
                             "draining out politely, all memory returned")
                    self.stop_serving()
                elif action == "rejoin":
                    self.log(f"machine calm again (~{load:.0f}%) -> rejoining the network")
                    if not self.start_serving():
                        self.log("coordinator unreachable - staying drained, will retry")
                        self.stop_serving(polite=False)
                time.sleep(SENSOR_PERIOD_S)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop_serving()
            self.log("left the network (credits are kept - withdrawal is never punished)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Sanad polite node (rpc-server wrapper)")
    ap.add_argument("--node-id", required=True)
    ap.add_argument("--operator", required=True, help="account that earns this node's credits")
    ap.add_argument("--host", default="127.0.0.1",
                    help="address the rpc-server listens on; use 0.0.0.0 to accept "
                         "a coordinator on your local network")
    ap.add_argument("--advertise", default=None,
                    help="address the coordinator should dial (defaults to your LAN IP "
                         "when --host is 0.0.0.0)")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--pledge-mb", type=float, default=800,
                    help="RAM lent to the network; layer share and earnings follow it")
    ap.add_argument("--busy-at", type=float, default=55,
                    help="drain out when other-process CPU%% stays above this (101 = dedicated)")
    ap.add_argument("--resume-at", type=float, default=25,
                    help="rejoin when other-process CPU%% stays below this")
    ap.add_argument("--coordinator", default=None,
                    help="coordinator URL; omit with --discover to find one on your LAN")
    ap.add_argument("--discover", action="store_true",
                    help="find the coordinator by asking the local network")
    ap.add_argument("--rpc-bin", required=True, help="directory containing ggml-rpc-server")
    args = ap.parse_args()

    if args.discover or not args.coordinator:
        from .discovery import discover
        print("[sanad-node] looking for a coordinator on this network...", flush=True)
        found = discover()
        if not found:
            sys.exit("no coordinator answered on this network. Start one, or pass "
                     "--coordinator http://<its-address>:7860")
        if len(found) > 1:
            print("[sanad-node] several coordinators answered; using the first:")
            for f in found:
                print(f"    {f['name']}  {f['coordinator']}")
        args.coordinator = found[0]["coordinator"]
        print(f"[sanad-node] found '{found[0]['name']}' at {args.coordinator}")
        if args.host == "127.0.0.1" and not found[0]["from"].startswith("127."):
            args.host = "0.0.0.0"    # the coordinator is remote: listen for it
    PoliteNode(args).run()


if __name__ == "__main__":
    main()
