"""Sanad node v0.2 — the polite node.

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


def total_cpu_percent() -> float:
    """System-wide CPU load. PowerShell keeps this stdlib-only on Windows."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average"],
        capture_output=True, text=True, timeout=20,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def proc_cpu_seconds(pid: int) -> float:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).TotalProcessorTime.TotalSeconds"],
        capture_output=True, text=True, timeout=20,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


class PoliteNode:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.bin_dir = Path(args.rpc_bin).resolve()
        self.rpc_exe = self.bin_dir / "ggml-rpc-server.exe"
        if not self.rpc_exe.exists():
            sys.exit(f"ggml-rpc-server.exe not found in {self.bin_dir}")
        self.proc: subprocess.Popen | None = None
        self.serving = False
        self.n_cores = self._core_count()

    @staticmethod
    def _core_count() -> int:
        import os
        return os.cpu_count() or 1

    def log(self, msg: str) -> None:
        print(f"[{self.args.node_id}] {msg}", flush=True)

    # -- rpc-server lifecycle ------------------------------------------------
    def start_serving(self) -> None:
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
        for _ in range(30):
            try:
                post(f"{self.args.coordinator}/register", payload)
                break
            except Exception:
                time.sleep(1.0)
        else:
            self.stop_serving(polite=False)
            sys.exit(f"could not reach coordinator at {self.args.coordinator}")
        self.serving = True
        self.log(f"SERVING on {self.args.host}:{self.args.port} "
                 f"(pledge {self.args.pledge_mb} MB, BELOW_NORMAL priority, operator {self.args.operator})")

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

    # -- busy sensor -----------------------------------------------------------
    def other_load_percent(self) -> float:
        """CPU load excluding our own rpc-server: what the OWNER is using."""
        pid = self.proc.pid if self.proc else None
        c0 = proc_cpu_seconds(pid) if pid else 0.0
        t0 = time.time()
        total = total_cpu_percent()  # ~1s sampling call doubles as the interval
        dt = max(time.time() - t0, 0.25)
        c1 = proc_cpu_seconds(pid) if pid else 0.0
        own = ((c1 - c0) / dt / self.n_cores) * 100.0
        return max(total - own, 0.0)

    # -- main loop -------------------------------------------------------------
    def run(self) -> None:
        self.start_serving()
        busy_streak = 0
        calm_streak = 0
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

                load = self.other_load_percent()
                if self.serving:
                    busy_streak = busy_streak + 1 if load >= self.args.busy_at else 0
                    if busy_streak >= BUSY_SAMPLES:
                        self.log(f"owner is busy (other-process load ~{load:.0f}%) -> "
                                 "draining out politely, all memory returned")
                        self.stop_serving()
                        busy_streak = 0
                else:
                    calm_streak = calm_streak + 1 if load <= self.args.resume_at else 0
                    if calm_streak >= CALM_SAMPLES:
                        self.log(f"machine calm again (~{load:.0f}%) -> rejoining the network")
                        self.start_serving()
                        calm_streak = 0
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
