"""Sanad coordinator v0.2.1 — "the living network", hardened.

v0.2 added: capacity ladder, memory pledges, layer-share weighted credits,
graceful membership, wallet-style statement.

v0.2.1 hardens the scheduler and accounting after an adversarial review:
- **Anti-starvation**: every third queue slot is served strictly
  first-come-first-served regardless of credits, so zero-credit users are
  served within bounded time even under sustained contributor load.
- **Escrow accounting**: a job's expected cost (max_tokens) is escrowed at
  submit and settled after the run (refund or nothing further); cancelled,
  timed-out, and failed jobs are refunded. Concurrent jobs can no longer all
  inherit priority from the same unspent balance.
- **Cancellation**: a job whose submitter timed out waiting is skipped (and
  refunded) instead of running unobserved and charging for a discarded result.
- **Distinct failures**: CapacityError (no nodes / pool too small) fails fast;
  ChainFailure (pipeline execution failed, incl. engine timeout) triggers one
  repair retry that first evicts pipeline nodes with no heartbeat since the
  job started, then waits past a heartbeat period so live nodes re-appear.
- **Accounting guard**: if the engine log cannot prove who served which
  layers, nobody is charged and nobody is paid — an event records the loss.
- **Thread-safe ladder events**; /status no longer mutates the event log.

Still honest v0: centralized-but-open coordinator, trusted operators and
trusted clients (identity unauthenticated), one inference at a time, model
reloaded per request. See docs/ARCHITECTURE.md.

Usage:
    python -m sanad_net.coordinator --port 7860 \
        --models ../.local/models/qwen2.5-0.5b-instruct-q4_k_m.gguf,../.local/models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
        --llama-bin ../.local/bin
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .ledger import Ledger

NODE_TTL_S = 15.0          # node considered dead if no heartbeat within this window
POOL_SAFETY_FACTOR = 1.4   # model needs file_size * factor of pooled memory (KV + overhead)
MAX_TOKENS_CAP = 512       # per-request generation cap
MAX_PLEDGE_MB = 1_000_000  # 1 TB — sanity bound; also rejects Infinity/NaN smuggled via JSON
MAX_BODY_BYTES = 1_048_576  # 1 MB request-body cap


class CapacityError(RuntimeError):
    """No pipeline can be built (no nodes, or pool too small). Not repairable."""


class ChainFailure(RuntimeError):
    """The assembled pipeline failed to execute. Repairable once.

    May carry `node_ids` (the pipeline that failed) and `job_start_ts`.
    """

    node_ids: list[str] = []
    job_start_ts: float = 0.0


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

    def suspect(self, node_id: str) -> None:
        """Mark a node dead-until-next-heartbeat (used after a chain failure)."""
        with self._lock:
            node = self._nodes.get(node_id)
            if node is not None:
                node["last_seen"] = 0.0

    def last_seen(self, node_id: str) -> float:
        with self._lock:
            node = self._nodes.get(node_id)
            return node["last_seen"] if node else 0.0

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
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=600, cwd=str(self.bin_dir),
            )
        except subprocess.TimeoutExpired as exc:
            raise ChainFailure(f"llama-completion timed out after 600s") from exc
        wall_s = time.time() - t0
        if proc.returncode != 0:
            raise ChainFailure(f"llama-completion failed (rc={proc.returncode}): {proc.stderr[-800:]}")
        stats = parse_llama_log(proc.stderr)
        return {"text": proc.stdout.strip(), "wall_s": round(wall_s, 2), **stats}


class Coordinator:
    RETRY_DELAY_S = 4.0        # > heartbeat period, so live nodes re-appear before the retry
    FIFO_EVERY = 3             # every Nth slot is strictly first-come-first-served

    def __init__(self, runner: InferenceRunner, catalog: list[ModelTier]) -> None:
        self.registry = Registry()
        self.ledger = Ledger()
        self.runner = runner
        self.catalog = catalog
        self.jobs_done = 0
        self.events: deque[dict] = deque(maxlen=1000)  # bounded audit trail
        self._last_tier: str | None = None
        self._tier_lock = threading.Lock()
        self._pending: list[dict] = []
        self._cv = threading.Condition()
        self._served = 0
        self._seq = 0
        threading.Thread(target=self._worker_loop, daemon=True).start()

    # -- capacity ladder -----------------------------------------------------
    def _compute_tier(self) -> tuple[ModelTier | None, list[dict], float]:
        """Pure: no event emission. Safe for /status."""
        nodes = self.registry.alive()
        pool_mb = sum(n["pledge_mb"] for n in nodes)
        return pick_tier(self.catalog, pool_mb), nodes, pool_mb

    def current_tier(self) -> tuple[ModelTier | None, list[dict], float]:
        tier, nodes, pool_mb = self._compute_tier()
        name = tier.name if tier else None
        with self._tier_lock:
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
    def submit(self, user: str, prompt: str, max_tokens: int, timeout_s: float = 900.0) -> dict:
        """Blocking: enqueue with credit priority (escrowed), wait for the result."""
        with self._cv:
            self._seq += 1
            seq = self._seq
        priority = -self.ledger.balance(user)  # higher balance -> served first
        escrow = self.ledger.spend(user, float(max_tokens), f"escrow for job #{seq}")
        job = {
            "seq": seq, "priority": priority, "user": user, "prompt": prompt,
            "max_tokens": max_tokens, "escrow": escrow,
            "done": threading.Event(), "slot": {}, "cancelled": False, "started": False,
        }
        with self._cv:
            self._pending.append(job)
            self._cv.notify()
        job["done"].wait(timeout=timeout_s)
        if not job["done"].is_set():
            with self._cv:
                if not job["started"]:
                    job["cancelled"] = True
                    if escrow > 0:
                        self.ledger.earn(user, escrow, f"escrow refund (job #{seq} timed out in queue)")
            raise TimeoutError("job timed out in queue")
        if "error" in job["slot"]:
            raise RuntimeError(job["slot"]["error"])
        return job["slot"]["result"]

    def _pop_next_locked(self) -> dict:
        """Pick the next job. Every FIFO_EVERY-th slot is strictly by arrival
        order — the anti-starvation reserve that keeps zero-credit users moving."""
        self._served += 1
        if self._served % self.FIFO_EVERY == 0:
            job = min(self._pending, key=lambda j: j["seq"])
        else:
            job = min(self._pending, key=lambda j: (j["priority"], j["seq"]))
        self._pending.remove(job)
        return job

    def _worker_loop(self) -> None:
        while True:
            with self._cv:
                while not self._pending:
                    self._cv.wait()
                job = self._pop_next_locked()
                if job["cancelled"]:
                    self._event(f"job #{job['seq']} cancelled before start - skipped (escrow refunded)")
                    job["done"].set()
                    continue
                job["started"] = True
            try:
                job["slot"]["result"] = self._run_job_with_repair(job)
            except Exception as exc:
                job["slot"]["error"] = f"{type(exc).__name__}: {exc}"
                if job["escrow"] > 0:
                    self.ledger.earn(job["user"], job["escrow"],
                                     f"escrow refund (job #{job['seq']} failed)")
            finally:
                job["done"].set()

    def _run_job_with_repair(self, job: dict) -> dict:
        """Chain repair: on pipeline failure, evict nodes with no heartbeat since
        the job started (likely dead), wait past a heartbeat period so live
        nodes re-appear, and retry once with the survivors. Capacity errors
        fail fast — retrying cannot create nodes."""
        try:
            return self._run_job(job)
        except ChainFailure as exc:
            stale = [nid for nid in exc.node_ids
                     if self.registry.last_seen(nid) < exc.job_start_ts]
            for nid in stale:
                self.registry.suspect(nid)
            self._event(f"chain failed - suspected {stale or 'no'} stale nodes; "
                        "retrying with survivors")
            time.sleep(self.RETRY_DELAY_S)
            return self._run_job(job, repaired=True)

    def _run_job(self, job: dict, repaired: bool = False) -> dict:
        tier, nodes, pool_mb = self.current_tier()
        if not nodes:
            raise CapacityError("no live nodes registered - start sanad_net.node first")
        if tier is None:
            raise CapacityError(f"pool of {pool_mb:.0f} MB cannot hold any catalog model")

        rpc_servers = [f"{n['host']}:{n['port']}" for n in nodes]
        tensor_split = [n["pledge_mb"] for n in nodes]  # layer share follows the pledge
        job_start_ts = time.time()
        try:
            result = self.runner.run(tier.path, job["prompt"], rpc_servers,
                                     tensor_split, job["max_tokens"])
        except ChainFailure as exc:
            exc.node_ids = [n["node_id"] for n in nodes]
            exc.job_start_ts = job_start_ts
            raise

        # Layer-share weighted credits: memory lent == layers held == share earned.
        tokens = result["decode_tokens"]
        shard_map = result.get("shard_map", {})
        known_devices = {f"RPC{i}" for i in range(len(nodes))}
        provable = tokens > 0 and shard_map and set(shard_map) <= known_devices
        if provable:
            total_layers = sum(d["n_layers"] for d in shard_map.values())
            # RPC device order follows the --rpc list order == `nodes` order.
            for i, n in enumerate(nodes):
                n_layers = shard_map.get(f"RPC{i}", {}).get("n_layers", 0)
                share = tokens * (n_layers / total_layers)
                if share > 0:
                    self.ledger.earn(
                        n["operator"], round(share, 3),
                        f"served {n_layers}/{total_layers} layers of {tier.name} for {tokens} tokens via {n['node_id']}",
                    )
            # Settle escrow: refund the unused part; low-balance users owe nothing more.
            refund = job["escrow"] - float(tokens)
            if refund > 0:
                self.ledger.earn(job["user"], refund, f"escrow refund (job #{job['seq']} used {tokens} tokens)")
        else:
            # Unproven serving is unpaid serving — and an uncharged request.
            if job["escrow"] > 0:
                self.ledger.earn(job["user"], job["escrow"],
                                 f"escrow refund (job #{job['seq']}: shard map unprovable)")
            self._event(f"WARNING job #{job['seq']}: engine log unprovable "
                        f"(tokens={tokens}, devices={sorted(shard_map)}) - no credits minted or charged")
        self.jobs_done += 1
        return {
            "user": job["user"],
            "model": tier.name,
            "priority_at_submit": -job["priority"],
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
        tier, nodes, pool_mb = self._compute_tier()
        return {
            "model": tier.name if tier else None,
            "pool_mb": round(pool_mb, 1),
            "catalog": [{"name": t.name, "file_mb": round(t.file_mb, 1), "need_mb": round(t.need_mb, 1)}
                        for t in self.catalog],
            "nodes": nodes,
            "balances": self.ledger.balances(),
            "jobs_done": self.jobs_done,
            "events": list(self.events)[-20:],
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
            if length > MAX_BODY_BYTES:
                raise ValueError(f"request body too large ({length} bytes)")
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
                    pledge = float(data.get("pledge_mb", 800))
                    # Python's json accepts Infinity/NaN — an infinite pledge would
                    # pin the ladder to the largest tier and NaN would poison math.
                    if not math.isfinite(pledge) or not 0 < pledge <= MAX_PLEDGE_MB:
                        raise ValueError(f"pledge_mb must be a finite value in (0, {MAX_PLEDGE_MB}]")
                    coord.registry.register(
                        data["node_id"], data["host"], data["port"],
                        data["operator"], pledge,
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
                    raw_tokens = data.get("max_tokens", 48)
                    if isinstance(raw_tokens, float) and not math.isfinite(raw_tokens):
                        raise ValueError("max_tokens must be finite")
                    max_tokens = max(1, min(int(raw_tokens), MAX_TOKENS_CAP))
                    result = coord.submit(data.get("user", "anon"), data["prompt"], max_tokens)
                    self._json(200, result)
                else:
                    self._json(404, {"error": "unknown path"})
            except Exception as exc:
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description="Sanad coordinator")
    ap.add_argument("--port", type=int, default=7860)
    ap.add_argument("--bind", default="127.0.0.1",
                    help="bind address (default loopback; anything else is at your own risk in v0)")
    ap.add_argument("--models", required=True,
                    help="comma-separated GGUF paths, the capacity-ladder catalog")
    ap.add_argument("--llama-bin", required=True, help="directory containing llama-completion.exe")
    args = ap.parse_args()

    catalog = load_catalog(args.models.split(","))
    runner = InferenceRunner(Path(args.llama_bin).resolve())
    coord = Coordinator(runner, catalog)
    if args.bind != "127.0.0.1":
        print("[sanad-coordinator] WARNING: binding beyond loopback — v0 has no "
              "authentication and llama.cpp's RPC backend is not hardened. "
              "Trusted networks only.")
    server = ThreadingHTTPServer((args.bind, args.port), make_handler(coord))
    tiers = " -> ".join(f"{t.name} (needs {t.need_mb:.0f} MB)" for t in catalog)
    print(f"[sanad-coordinator] http://{args.bind}:{args.port}  ladder: {tiers}")
    server.serve_forever()


if __name__ == "__main__":
    main()
