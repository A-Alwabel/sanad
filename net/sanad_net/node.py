"""Sanad node v0.2.1 — the polite node, hardened.

Wraps a ggml-rpc-server process and registers it with the coordinator, with
"good guest" behavior baked in:

- **Memory pledge** (--pledge-mb): the operator declares how much RAM to lend.
  The coordinator enforces the matching layer share via --tensor-split, and
  credits are earned in proportion to it.
- **Low OS priority**: the rpc-server runs BELOW_NORMAL, so anything the owner
  opens (a game, a browser, work) takes the CPU first — enforced by the OS
  itself, not by our goodwill.
- **Busy sensor** (--busy-at / --resume-at): samples CPU load from processes
  OTHER than our own rpc-server. Sustained high load means the owner needs the
  machine: the node drains out gracefully (/leave + rpc-server stopped, all
  memory returned). When the machine has been calm for a while, it rejoins and
  re-registers automatically. Withdrawal is never punished: credits are kept.
  Set --busy-at 101 for a dedicated (always-on) node.

v0.2.1 hardening (post adversarial review):
- Sensor failures no longer fail open: an unreadable sample is treated as
  "unknown" (state kept, streaks reset) instead of 0% load, and a sensor error
  can never crash the node process.
- A drained node that cannot reach the coordinator on rejoin stays drained and
  keeps trying, instead of exiting.
- The sensor's decision logic lives in a pure, unit-tested state machine
  (SensorFSM).

Usage:
    python -m sanad_net.node --node-id riyadh-a --operator amina \
        --port 50070 --pledge-mb 1000 --coordinator http://127.0.0.1:7860 \
        --rpc-bin ../.local/bin
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

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


class CpuSampler:
    """Native Windows CPU sampling via kernel32 (ctypes) — no subprocesses.

    The first sensor design shelled out to PowerShell; under the exact
    condition it was meant to detect (a saturated CPU) PowerShell itself
    starved and timed out, so the sensor went blind precisely when it
    mattered. GetSystemTimes/GetProcessTimes are microsecond-cheap kernel
    calls that keep working under any load.

    Loads are measured between consecutive calls (the sensor loop period).
    Returns None on the first call and on API failure.
    """

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def __init__(self) -> None:
        import ctypes
        self._ct = ctypes
        self._k32 = ctypes.windll.kernel32
        self._prev_sys: tuple[int, int, int] | None = None
        self._prev_proc: tuple[int, float] | None = None  # (cpu_100ns, wall_ts)
        import os
        self._n_cores = os.cpu_count() or 1

    def _filetimes(self, n: int):
        class FILETIME(self._ct.Structure):
            _fields_ = [("lo", self._ct.c_uint32), ("hi", self._ct.c_uint32)]
        return [FILETIME() for _ in range(n)]

    @staticmethod
    def _val(ft) -> int:
        return (ft.hi << 32) | ft.lo

    def _system_times(self) -> tuple[int, int, int] | None:
        idle, kern, user = self._filetimes(3)
        if not self._k32.GetSystemTimes(self._ct.byref(idle), self._ct.byref(kern), self._ct.byref(user)):
            return None
        return self._val(idle), self._val(kern), self._val(user)

    def _proc_cpu_100ns(self, pid: int) -> int | None:
        handle = self._k32.OpenProcess(self._PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            created, exited, kern, user = self._filetimes(4)
            ok = self._k32.GetProcessTimes(handle, self._ct.byref(created), self._ct.byref(exited),
                                           self._ct.byref(kern), self._ct.byref(user))
            if not ok:
                return None
            return self._val(kern) + self._val(user)
        finally:
            self._k32.CloseHandle(handle)

    def other_load_percent(self, own_pid: int | None) -> float | None:
        """CPU load excluding the process `own_pid` (our rpc-server): what the
        OWNER is using. None until two samples exist or on API failure."""
        try:
            sys_now = self._system_times()
            proc_now = self._proc_cpu_100ns(own_pid) if own_pid else 0
            now = time.time()
        except Exception:
            self._prev_sys = None
            self._prev_proc = None
            return None
        if sys_now is None or proc_now is None:
            self._prev_sys = None
            self._prev_proc = None
            return None

        result: float | None = None
        if self._prev_sys is not None and self._prev_proc is not None:
            d_idle = sys_now[0] - self._prev_sys[0]
            d_kern = sys_now[1] - self._prev_sys[1]
            d_user = sys_now[2] - self._prev_sys[2]
            d_total = d_kern + d_user  # kernel time includes idle time
            d_wall = max(now - self._prev_proc[1], 0.25)
            if d_total > 0:
                total_pct = (1.0 - d_idle / d_total) * 100.0
                own_pct = ((proc_now - self._prev_proc[0]) / 1e7 / d_wall / self._n_cores) * 100.0
                result = max(total_pct - own_pct, 0.0)
        self._prev_sys = sys_now
        self._prev_proc = (proc_now, now)
        return result


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
            self.busy_streak = 0
            self.calm_streak = 0
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


class PoliteNode:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.bin_dir = Path(args.rpc_bin).resolve()
        self.rpc_exe = self.bin_dir / "ggml-rpc-server.exe"
        if not self.rpc_exe.exists():
            sys.exit(f"ggml-rpc-server.exe not found in {self.bin_dir}")
        self.proc: subprocess.Popen | None = None
        self.serving = False
        self.fsm = SensorFSM(args.busy_at, args.resume_at)
        self.sampler = CpuSampler() if args.busy_at <= 100 else None

    def log(self, msg: str) -> None:
        print(f"[{self.args.node_id}] {msg}", flush=True)

    # -- rpc-server lifecycle ------------------------------------------------
    def start_serving(self) -> bool:
        """Spawn rpc-server and register. Returns False (and cleans up) on failure."""
        creationflags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
        self.proc = subprocess.Popen(
            [str(self.rpc_exe), "-H", self.args.host, "-p", str(self.args.port),
             "-t", str(self.args.threads)],
            cwd=str(self.bin_dir),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=creationflags,  # the owner's apps always win the CPU
        )
        payload = {
            "node_id": self.args.node_id, "host": self.args.host,
            "port": self.args.port, "operator": self.args.operator,
            "pledge_mb": self.args.pledge_mb,
        }
        for _ in range(10):
            try:
                post(f"{self.args.coordinator}/register", payload)
                self.serving = True
                self.log(f"SERVING on {self.args.host}:{self.args.port} "
                         f"(pledge {self.args.pledge_mb} MB, BELOW_NORMAL priority, "
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

                if self.args.busy_at > 100:  # dedicated node: sensor off
                    time.sleep(SENSOR_PERIOD_S)
                    continue

                try:
                    load = self.sampler.other_load_percent(self.proc.pid if self.proc else None)
                except Exception:
                    load = None  # a sensor error must never crash the node
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
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--pledge-mb", type=float, default=800,
                    help="RAM lent to the network; layer share and earnings follow it")
    ap.add_argument("--busy-at", type=float, default=55,
                    help="drain out when other-process CPU%% stays above this (101 = dedicated node)")
    ap.add_argument("--resume-at", type=float, default=25,
                    help="rejoin when other-process CPU%% stays below this")
    ap.add_argument("--coordinator", default="http://127.0.0.1:7860")
    ap.add_argument("--rpc-bin", required=True, help="directory containing ggml-rpc-server.exe")
    PoliteNode(ap.parse_args()).run()


if __name__ == "__main__":
    main()
