"""Sanad coordinator: node registry, credit-priority job queue, real inference.

Runs a single-process HTTP service (stdlib only). Inference is delegated to
llama.cpp's `llama-cli --rpc host:port,host:port`, which splits the model's
layers across the registered nodes' ggml-rpc-server processes over TCP.

v0 honesty notes (see docs/ARCHITECTURE.md):
- The coordinator is centralized-but-open, like AI Horde's. Federation is later work.
- Credits are minted per generated token and split evenly across serving nodes
  (per-layer-share weighting is future work; the simulation already models it).
- One inference at a time; queued jobs are ordered by requester balance —
  contributors get priority, anonymous users are served last but always served.

Usage:
    python -m sanad_net.coordinator --port 7860 \
        --model ../.local/models/qwen2.5-0.5b-instruct-q4_k_m.gguf \
        --llama-bin ../.local/bin
"""

from __future__ import annotations

import argparse
import json
import queue
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .ledger import Ledger

NODE_TTL_S = 15.0  # node considered dead if no heartbeat within this window


class Registry:
    def __init__(self) -> None:
        self._nodes: dict[str, dict] = {}
        self._lock = threading.Lock()

    def register(self, node_id: str, host: str, port: int, operator: str) -> None:
        with self._lock:
            self._nodes[node_id] = {
                "node_id": node_id,
                "host": host,
                "port": int(port),
                "operator": operator,
                "last_seen": time.time(),
            }

    def heartbeat(self, node_id: str) -> bool:
        with self._lock:
            node = self._nodes.get(node_id)
            if node is None:
                return False
            node["last_seen"] = time.time()
            return True

    def alive(self) -> list[dict]:
        now = time.time()
        with self._lock:
            return sorted(
                (dict(n) for n in self._nodes.values() if now - n["last_seen"] <= NODE_TTL_S),
                key=lambda n: n["node_id"],
            )


def parse_llama_log(stderr_text: str) -> dict:
    """Extract sharding proof and performance numbers from llama.cpp's log.

    Format observed on llama.cpp b10276 (verbose mode):
      ... llama_prepare_model_devices: using device RPC0 (127.0.0.1:50060) ...
      ... load_tensors: layer   5 assigned to device RPC0, is_swa = 0
      ... common_perf_print:        eval time = 1025.62 ms / 31 runs (33.08 ms per token, 30.23 tokens per second)
    """
    devices: dict[str, str] = {}
    # llama.cpp logs each layer assignment once per load pass (2 passes observed);
    # keep only the LAST assignment per layer — that is the final placement.
    final_assignment: dict[int, str] = {}
    tokens = 0
    tok_per_s = 0.0
    for ln in stderr_text.splitlines():
        m = re.search(r"using device (RPC\d+) \(([^)]+)\)", ln)
        if m:
            devices[m.group(1)] = m.group(2)
            continue
        m = re.search(r"load_tensors: layer\s+(\d+) assigned to device (\S+?),", ln)
        if m:
            final_assignment[int(m.group(1))] = m.group(2)
            continue
        if "eval time" in ln and "prompt eval" not in ln:
            m = re.search(
                r"eval time\s*=\s*[\d.]+\s*ms\s*/\s*(\d+)\s*runs?\s*\(\s*[\d.]+\s*ms per token,\s*([\d.]+)\s*tokens per second",
                ln,
            )
            if m:
                tokens = int(m.group(1))
                tok_per_s = float(m.group(2))
    layers: dict[str, list[int]] = {}
    for layer, dev in final_assignment.items():
        layers.setdefault(dev, []).append(layer)
    shard_map = {
        dev: {
            "endpoint": devices.get(dev, "?"),
            "layers": f"{min(idx)}-{max(idx)}",
            "n_layers": len(idx),
        }
        for dev, idx in sorted(layers.items())
    }
    return {"shard_map": shard_map, "decode_tokens": tokens, "tok_per_s": tok_per_s}


class InferenceRunner:
    """Wraps llama-cli --rpc. Injectable for tests."""

    def __init__(self, llama_bin_dir: Path, model_path: Path) -> None:
        # llama.cpp >= b10xxx: llama-cli is interactive-first; llama-completion
        # is the non-interactive one-shot tool suited to subprocess use.
        self.llama_bin = llama_bin_dir / "llama-completion.exe"
        self.model_path = model_path
        self.bin_dir = llama_bin_dir

    def run(self, prompt: str, rpc_servers: list[str], max_tokens: int) -> dict:
        cmd = [
            str(self.llama_bin),
            "-m", str(self.model_path),
            "--rpc", ",".join(rpc_servers),
            "-ngl", "99",
            "-n", str(max_tokens),
            "-p", prompt,
            "--temp", "0",
            "--seed", "42",
            "-no-cnv",
            "--no-display-prompt",
            "--simple-io",
            "-v",  # verbose: exposes per-layer device assignment for the shard map
        ]
        t0 = time.time()
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=600, cwd=str(self.bin_dir),
        )
        wall_s = time.time() - t0
        if proc.returncode != 0:
            raise RuntimeError(f"llama-cli failed (rc={proc.returncode}): {proc.stderr[-800:]}")
        stats = parse_llama_log(proc.stderr)
        return {"text": proc.stdout.strip(), "wall_s": round(wall_s, 2), **stats}


class Coordinator:
    def __init__(self, runner: InferenceRunner) -> None:
        self.registry = Registry()
        self.ledger = Ledger()
        self.runner = runner
        self.jobs_done = 0
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = 0
        self._seq_lock = threading.Lock()
        threading.Thread(target=self._worker_loop, daemon=True).start()

    # -- job scheduling ------------------------------------------------------
    def submit(self, user: str, prompt: str, max_tokens: int) -> dict:
        """Blocking: enqueue with credit priority, wait for this job's result."""
        with self._seq_lock:
            self._seq += 1
            seq = self._seq
        priority = -self.ledger.balance(user)  # higher balance -> lower tuple -> served first
        done = threading.Event()
        slot: dict = {}
        self._queue.put((priority, seq, user, prompt, max_tokens, done, slot))
        done.wait(timeout=900)
        if not done.is_set():
            raise TimeoutError("job timed out in queue")
        if "error" in slot:
            raise RuntimeError(slot["error"])
        return slot["result"]

    def _worker_loop(self) -> None:
        while True:
            priority, seq, user, prompt, max_tokens, done, slot = self._queue.get()
            try:
                slot["result"] = self._run_job(user, prompt, max_tokens, priority)
            except Exception as exc:  # surface to the waiting request thread
                slot["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                done.set()

    def _run_job(self, user: str, prompt: str, max_tokens: int, priority: float) -> dict:
        nodes = self.registry.alive()
        if not nodes:
            raise RuntimeError("no live nodes registered — start sanad_net.node first")
        rpc_servers = [f"{n['host']}:{n['port']}" for n in nodes]
        result = self.runner.run(prompt, rpc_servers, max_tokens)

        tokens = result["decode_tokens"]
        if tokens > 0:
            share = tokens / len(nodes)
            for n in nodes:
                self.ledger.earn(n["operator"], share, f"served {share:g} of {tokens} tokens via {n['node_id']}")
            self.ledger.spend(user, float(tokens), f"inference of {tokens} tokens")
        self.jobs_done += 1
        return {
            "user": user,
            "priority_at_submit": -priority,
            "pipeline": [{"node_id": n["node_id"], "endpoint": f"{n['host']}:{n['port']}", "operator": n["operator"]} for n in nodes],
            **result,
        }

    def status(self) -> dict:
        return {
            "nodes": self.registry.alive(),
            "balances": self.ledger.balances(),
            "jobs_done": self.jobs_done,
        }


def make_handler(coord: Coordinator):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet
            pass

        def _json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self):
            if self.path == "/status":
                self._json(200, coord.status())
            else:
                self._json(404, {"error": "unknown path"})

        def do_POST(self):
            try:
                data = self._read_body()
                if self.path == "/register":
                    coord.registry.register(data["node_id"], data["host"], data["port"], data["operator"])
                    self._json(200, {"ok": True})
                elif self.path == "/heartbeat":
                    ok = coord.registry.heartbeat(data["node_id"])
                    self._json(200 if ok else 404, {"ok": ok})
                elif self.path == "/ask":
                    result = coord.submit(
                        data.get("user", "anon"), data["prompt"], int(data.get("max_tokens", 48))
                    )
                    self._json(200, result)
                else:
                    self._json(404, {"error": "unknown path"})
            except Exception as exc:
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description="Sanad coordinator")
    ap.add_argument("--port", type=int, default=7860)
    ap.add_argument("--model", required=True)
    ap.add_argument("--llama-bin", required=True, help="directory containing llama-cli.exe")
    args = ap.parse_args()

    runner = InferenceRunner(Path(args.llama_bin).resolve(), Path(args.model).resolve())
    coord = Coordinator(runner)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(coord))
    print(f"[sanad-coordinator] listening on http://127.0.0.1:{args.port}  model={runner.model_path.name}")
    server.serve_forever()


if __name__ == "__main__":
    main()
