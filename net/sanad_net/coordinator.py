"""Sanad coordinator v0.2 — "the living network".

Adds to v0.1 (First Light):
- **Capacity ladder**: a catalog of models; the coordinator always serves the
  largest model the current pool of pledged memory can hold, upgrading when
  nodes join and downgrading when they leave. The network grows as its
  community grows.
- **Memory pledges**: nodes declare how much RAM they lend; layer shares are
  enforced proportionally via llama.cpp's --tensor-split (verified empirically:
  a 3,1 split yields ~3:1 layer assignment).
- **Layer-share weighted credits**: each generated token mints 1 credit split
  by the share of model layers each node actually held (memory lent == share
  earned), replacing v0.1's even split.
- **Graceful membership**: /leave endpoint for polite nodes draining out;
  chain repair — a job that fails because a node vanished is retried once with
  the surviving pipeline.
- **Wallet-style statement**: /ledger returns the full audited entry history.

Still honest v0: centralized-but-open coordinator, trusted operators, one
inference at a time, model reloaded per request (which is precisely what makes
ladder switching free). See docs/ARCHITECTURE.md.

Usage:
    python -m sanad_net.coordinator --port 7860 \
        --models ../.local/models/qwen2.5-0.5b-instruct-q4_k_m.gguf,../.local/models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
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
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .ledger import Ledger

NODE_TTL_S = 15.0          # node considered dead if no heartbeat within this window
POOL_SAFETY_FACTOR = 1.4   # model needs file_size * factor of pooled memory (KV + overhead)


@dataclass(frozen=True)
class ModelTier:
    name: str
    path: Path
    file_mb: float

    @property
    def need_mb(self) -> float:
        return self.file_mb * POOL_SAFETY_FACTOR


def load_catalog(paths: list[str]) -> list[ModelTier]:
    tiers = []
    for p in paths:
        path = Path(p).resolve()
        tiers.append(ModelTier(name=path.stem, path=path, file_mb=path.stat().st_size / 1e6))
    return sorted(tiers, key=lambda t: t.file_mb)  # smallest first


class Registry:
    def __init__(self) -> None:
        self._nodes: dict[str, dict] = {}
        self._lock = threading.Lock()

    def register(self, node_id: str, host: str, port: int, operator: str, pledge_mb: float) -> None:
        with self._lock:
            self._nodes[node_id] = {
                "node_id": node_id,
                "host": host,
                "port": int(port),
                "operator": operator,
                "pledge_mb": float(pledge_mb),
                "last_seen": time.time(),
            }

    def heartbeat(self, node_id: str) -> bool:
        with self._lock:
            node = self._nodes.get(node_id)
            if node is None:
                return False
            node["last_seen"] = time.time()
            return True

    def leave(self, node_id: str) -> bool:
        """Graceful exit: the polite node drains out with zero penalty."""
        with self._lock:
            return self._nodes.pop(node_id, None) is not None

    def alive(self) -> list[dict]:
        now = time.time()
        with self._lock:
            return sorted(
                (dict(n) for n in self._nodes.values() if now - n["last_seen"] <= NODE_TTL_S),
                key=lambda n: n["node_id"],
            )


def pick_tier(catalog: list[ModelTier], pool_mb: float) -> ModelTier | None:
    """Largest model the pledged pool can hold — the capacity ladder."""
    fitting = [t for t in catalog if t.need_mb <= pool_mb]
    return fitting[-1] if fitting else None


def parse_llama_log(stderr_text: str) -> dict:
    """Extract sharding proof and performance numbers from llama.cpp's log.

    Format observed on llama.cpp b10276 (verbose mode):
      ... llama_prepare_model_devices: using device RPC0 (127.0.0.1:50060) ...
      ... load_tensors: layer   5 assigned to device RPC0, is_swa = 0
      ... common_perf_print:        eval time = 1025.62 ms / 31 runs (33.08 ms per token, 30.23 tokens per second)

    llama.cpp logs each layer assignment once per load pass (2 passes observed);
    only the LAST assignment per layer is the final placement.
    """
    devices: dict[str, str] = {}
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
    """Wraps llama-completion --rpc. Injectable for tests."""

    def __init__(self, llama_bin_dir: Path) -> None:
        # llama.cpp >= b10xxx: llama-cli is interactive-first; llama-completion
        # is the non-interactive one-shot tool suited to subprocess use.
        self.llama_bin = llama_bin_dir / "llama-completion.exe"
        self.bin_dir = llama_bin_dir

    def run(self, model_path: Path, prompt: str, rpc_servers: list[str],
            tensor_split: list[float], max_tokens: int) -> dict:
        cmd = [
            str(self.llama_bin),
            "-m", str(model_path),
            "--rpc", ",".join(rpc_servers),
            "-ngl", "99",
            "--tensor-split", ",".join(f"{s:g}" for s in tensor_split),
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
            raise RuntimeError(f"llama-completion failed (rc={proc.returncode}): {proc.stderr[-800:]}")
        stats = parse_llama_log(proc.stderr)
        return {"text": proc.stdout.strip(), "wall_s": round(wall_s, 2), **stats}


class Coordinator:
    def __init__(self, runner: InferenceRunner, catalog: list[ModelTier]) -> None:
        self.registry = Registry()
        self.ledger = Ledger()
        self.runner = runner
        self.catalog = catalog
        self.jobs_done = 0
        self.events: list[dict] = []
        self._last_tier: str | None = None
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = 0
        self._seq_lock = threading.Lock()
        threading.Thread(target=self._worker_loop, daemon=True).start()

    # -- capacity ladder -----------------------------------------------------
    def current_tier(self) -> tuple[ModelTier | None, list[dict], float]:
        nodes = self.registry.alive()
        pool_mb = sum(n["pledge_mb"] for n in nodes)
        tier = pick_tier(self.catalog, pool_mb)
        name = tier.name if tier else None
        if name != self._last_tier:
            direction = "UP" if (
                self._last_tier is None
                or (tier and any(t.name == self._last_tier and t.file_mb < tier.file_mb for t in self.catalog))
            ) else "DOWN"
            self._event(f"LADDER {direction}: model -> {name or 'none fits'} "
                        f"(pool {pool_mb:.0f} MB across {len(nodes)} nodes)")
            self._last_tier = name
        return tier, nodes, pool_mb

    def _event(self, msg: str) -> None:
        self.events.append({"ts": round(time.time(), 2), "event": msg})

    # -- job scheduling ------------------------------------------------------
    def submit(self, user: str, prompt: str, max_tokens: int) -> dict:
        """Blocking: enqueue with credit priority, wait for this job's result."""
        with self._seq_lock:
            self._seq += 1
            seq = self._seq
        priority = -self.ledger.balance(user)  # higher balance -> served first
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
                slot["result"] = self._run_job_with_repair(user, prompt, max_tokens, priority)
            except Exception as exc:
                slot["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                done.set()

    def _run_job_with_repair(self, user: str, prompt: str, max_tokens: int, priority: float) -> dict:
        """Chain repair: if the pipeline fails (e.g. a node vanished mid-job),
        rebuild from surviving nodes and retry once."""
        try:
            return self._run_job(user, prompt, max_tokens, priority)
        except RuntimeError:
            self._event("chain failed - rebuilding pipeline from surviving nodes and retrying")
            time.sleep(1.0)
            return self._run_job(user, prompt, max_tokens, priority, repaired=True)

    def _run_job(self, user: str, prompt: str, max_tokens: int, priority: float,
                 repaired: bool = False) -> dict:
        tier, nodes, pool_mb = self.current_tier()
        if not nodes:
            raise RuntimeError("no live nodes registered - start sanad_net.node first")
        if tier is None:
            raise RuntimeError(f"pool of {pool_mb:.0f} MB cannot hold any catalog model")

        rpc_servers = [f"{n['host']}:{n['port']}" for n in nodes]
        tensor_split = [n["pledge_mb"] for n in nodes]  # layer share follows the pledge
        result = self.runner.run(tier.path, prompt, rpc_servers, tensor_split, max_tokens)

        # Layer-share weighted credits: memory lent == layers held == share earned.
        tokens = result["decode_tokens"]
        shard_map = result.get("shard_map", {})
        total_layers = sum(d["n_layers"] for d in shard_map.values()) or 1
        if tokens > 0:
            # RPC device order follows the --rpc list order == `nodes` order.
            for i, n in enumerate(nodes):
                dev = f"RPC{i}"
                n_layers = shard_map.get(dev, {}).get("n_layers", 0)
                share = tokens * (n_layers / total_layers)
                if share > 0:
                    self.ledger.earn(
                        n["operator"], round(share, 3),
                        f"served {n_layers}/{total_layers} layers of {tier.name} for {tokens} tokens via {n['node_id']}",
                    )
            self.ledger.spend(user, float(tokens), f"inference of {tokens} tokens on {tier.name}")
        self.jobs_done += 1
        return {
            "user": user,
            "model": tier.name,
            "priority_at_submit": -priority,
            "repaired": repaired,
            "pool_mb": round(pool_mb, 1),
            "pipeline": [
                {"node_id": n["node_id"], "endpoint": f"{n['host']}:{n['port']}",
                 "operator": n["operator"], "pledge_mb": n["pledge_mb"]}
                for n in nodes
            ],
            **result,
        }

    def status(self) -> dict:
        tier, nodes, pool_mb = self.current_tier()
        return {
            "model": tier.name if tier else None,
            "pool_mb": round(pool_mb, 1),
            "catalog": [{"name": t.name, "file_mb": round(t.file_mb, 1), "need_mb": round(t.need_mb, 1)}
                        for t in self.catalog],
            "nodes": nodes,
            "balances": self.ledger.balances(),
            "jobs_done": self.jobs_done,
            "events": self.events[-20:],
        }


def make_handler(coord: Coordinator):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet
            pass

        def _json(self, code: int, payload) -> None:
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
            elif self.path.startswith("/ledger"):
                entries = [
                    {"ts": round(e.ts, 2), "account": e.account, "delta": e.delta, "reason": e.reason}
                    for e in coord.ledger.entries()
                ]
                self._json(200, {"entries": entries})
            else:
                self._json(404, {"error": "unknown path"})

        def do_POST(self):
            try:
                data = self._read_body()
                if self.path == "/register":
                    coord.registry.register(
                        data["node_id"], data["host"], data["port"],
                        data["operator"], data.get("pledge_mb", 800),
                    )
                    coord.current_tier()  # re-evaluate the ladder on join
                    self._json(200, {"ok": True})
                elif self.path == "/heartbeat":
                    ok = coord.registry.heartbeat(data["node_id"])
                    self._json(200 if ok else 404, {"ok": ok})
                elif self.path == "/leave":
                    ok = coord.registry.leave(data["node_id"])
                    coord.current_tier()  # re-evaluate the ladder on leave
                    self._json(200, {"ok": ok})
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
    ap.add_argument("--models", required=True,
                    help="comma-separated GGUF paths, the capacity-ladder catalog")
    ap.add_argument("--llama-bin", required=True, help="directory containing llama-completion.exe")
    args = ap.parse_args()

    catalog = load_catalog(args.models.split(","))
    runner = InferenceRunner(Path(args.llama_bin).resolve())
    coord = Coordinator(runner, catalog)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(coord))
    tiers = " -> ".join(f"{t.name} (needs {t.need_mb:.0f} MB)" for t in catalog)
    print(f"[sanad-coordinator] http://127.0.0.1:{args.port}  ladder: {tiers}")
    server.serve_forever()


if __name__ == "__main__":
    main()
