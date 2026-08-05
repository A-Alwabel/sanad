"""Sanad node: wraps a ggml-rpc-server process and registers with the coordinator.

Each node holds *part* of the model: llama.cpp streams the layer shards
assigned to this node into the rpc-server's memory over TCP at load time.

Usage:
    python -m sanad_net.node --node-id riyadh-a --operator amina \
        --port 50052 --coordinator http://127.0.0.1:7860 --rpc-bin ../.local/bin
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HEARTBEAT_S = 3.0


def post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def main() -> None:
    ap = argparse.ArgumentParser(description="Sanad node (rpc-server wrapper)")
    ap.add_argument("--node-id", required=True)
    ap.add_argument("--operator", required=True, help="account that earns this node's credits")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--coordinator", default="http://127.0.0.1:7860")
    ap.add_argument("--rpc-bin", required=True, help="directory containing ggml-rpc-server.exe")
    args = ap.parse_args()

    bin_dir = Path(args.rpc_bin).resolve()
    rpc_exe = bin_dir / "ggml-rpc-server.exe"
    if not rpc_exe.exists():
        sys.exit(f"ggml-rpc-server.exe not found in {bin_dir}")

    proc = subprocess.Popen(
        [str(rpc_exe), "-H", args.host, "-p", str(args.port), "-t", str(args.threads)],
        cwd=str(bin_dir),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print(f"[{args.node_id}] rpc-server pid={proc.pid} on {args.host}:{args.port} (operator: {args.operator})")

    def shutdown(*_):
        proc.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    payload = {"node_id": args.node_id, "host": args.host, "port": args.port, "operator": args.operator}
    for attempt in range(30):
        try:
            post(f"{args.coordinator}/register", payload)
            print(f"[{args.node_id}] registered with {args.coordinator}")
            break
        except Exception:
            time.sleep(1.0)
    else:
        proc.terminate()
        sys.exit(f"[{args.node_id}] could not reach coordinator at {args.coordinator}")

    def heartbeat_loop():
        while proc.poll() is None:
            try:
                post(f"{args.coordinator}/heartbeat", {"node_id": args.node_id})
            except Exception:
                pass
            time.sleep(HEARTBEAT_S)

    threading.Thread(target=heartbeat_loop, daemon=True).start()
    proc.wait()
    print(f"[{args.node_id}] rpc-server exited (rc={proc.returncode})")


if __name__ == "__main__":
    main()
