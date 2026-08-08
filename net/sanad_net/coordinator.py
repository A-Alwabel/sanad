"""Sanad coordinator v0.3 — "it actually works".

What changed from v0.2.1: the engine is now **resident**. v0.2.1 spawned a
fresh llama-completion per request, re-streaming the model to every node each
time (~5-6 s before the first token). Now a llama-server holds the sharded
pipeline in the nodes' memory and answers immediately, with tokens streaming as
they are produced. The pipeline is rebuilt only when it actually changes — a
node joining or leaving, or the capacity ladder moving tier.

Also new: a web chat UI anyone can use (GET /), server-sent-event streaming
(POST /ask/stream), and cross-platform node support.

Carried over from v0.2.1: capacity ladder, memory pledges enforced as layer
shares, layer-share-weighted credits, escrow accounting with refunds,
anti-starvation scheduling, graceful membership, wallet statement.

Honest v0 scope: coordinator is centralized-but-open, operators and clients are
trusted (identity is unauthenticated), one inference at a time. See
docs/ARCHITECTURE.md.

Usage:
    python -m sanad_net.coordinator --port 7860 --bind 0.0.0.0 \
        --models ../.local/models/qwen2.5-0.5b-instruct-q4_k_m.gguf,../.local/models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
        --llama-bin ../.local/bin
"""

from __future__ import annotations

import argparse
import json
import math
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .discovery import DISCOVERY_PORT, DiscoveryResponder
from .engine import EngineError, EngineManager
from .ledger import Ledger

NODE_TTL_S = 15.0           # node considered dead if no heartbeat within this window
MODEL_OVERHEAD = 1.15       # weights plus fixed runtime overhead
KV_PER_SLOT = 0.06          # KV cache per concurrent slot, as a fraction of weights
REPAIR_DEBOUNCE_S = 20.0    # one rebuild serves every failure inside this window
MAX_TOKENS_CAP = 512        # per-request generation cap
MAX_PLEDGE_MB = 1_000_000   # 1 TB — sanity bound; also rejects Infinity/NaN from JSON
MAX_BODY_BYTES = 1_048_576  # 1 MB request-body cap
MAX_MESSAGES = 40           # conversation turns kept per request
MAX_MESSAGE_CHARS = 8000    # per-message ceiling
WEBUI = Path(__file__).parent / "webui.html"


class CapacityError(RuntimeError):
    """No pipeline can be built (no nodes, or pool too small). Not repairable."""


class ChainFailure(RuntimeError):
    """The pipeline failed to execute. Repairable once."""

    node_ids: list[str] = []
    job_start_ts: float = 0.0


@dataclass(frozen=True)
class ModelTier:
    name: str
    path: Path
    file_mb: float
    slots: int = 1

    @property
    def need_mb(self) -> float:
        """Pooled memory this tier needs: weights, runtime overhead, and KV
        cache for every concurrent slot. Concurrency is not free — a ladder
        that ignores it will pick a model the pool cannot actually hold."""
        return self.file_mb * (MODEL_OVERHEAD + KV_PER_SLOT * self.slots)


def load_catalog(paths: list[str], slots: int = 1) -> list[ModelTier]:
    tiers = []
    for p in paths:
        path = Path(p).resolve()
        tiers.append(ModelTier(name=path.stem, path=path,
                               file_mb=path.stat().st_size / 1e6, slots=max(1, slots)))
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


class Coordinator:
    RETRY_DELAY_S = 4.0        # > heartbeat period, so live nodes re-appear before a retry
    FIFO_EVERY = 3             # every Nth slot goes to the least-recently-served account
    GUEST_RATE = 0.34          # a zero-credit job still accrues priority at ~1/3 speed
    CREDIT_HALF = 500.0        # credits for half the gap between guest and maximum rate

    def __init__(self, engines: EngineManager, catalog: list[ModelTier],
                 ledger: Ledger | None = None, concurrency: int = 4) -> None:
        self.registry = Registry()
        self.ledger = ledger if ledger is not None else Ledger()
        self.engines = engines
        self.catalog = catalog
        self.concurrency = max(1, concurrency)
        self.jobs_done = 0
        self.events: deque[dict] = deque(maxlen=1000)
        self._last_tier: str | None = None
        self._tier_lock = threading.Lock()
        self._pending: list[dict] = []
        self._cv = threading.Condition()
        self._slots = threading.Semaphore(self.concurrency)
        self._repair_lock = threading.Lock()
        self._last_repair = 0.0
        self._last_served_at: dict[str, float] = {}
        self._served = 0
        self._seq = 0
        engines.on_event = self._event
        threading.Thread(target=self._dispatch_loop, daemon=True).start()

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
                    or (tier and any(t.name == self._last_tier and t.file_mb < tier.file_mb
                                     for t in self.catalog))
                ) else "DOWN"
                self._event(f"LADDER {direction}: model -> {name or 'none fits'} "
                            f"(pool {pool_mb:.0f} MB across {len(nodes)} nodes)")
                self._last_tier = name
        return tier, nodes, pool_mb

    def _event(self, msg: str) -> None:
        self.events.append({"ts": round(time.time(), 2), "event": msg})

    # -- job scheduling ------------------------------------------------------
    def submit(self, user: str, prompt: str, max_tokens: int,
               timeout_s: float = 900.0, stream_q: "queue.Queue | None" = None,
               messages: list[dict] | None = None) -> dict:
        """Blocking: enqueue with credit priority (escrowed), wait for the result.

        `messages` carries a full conversation (OpenAI-style role/content list)
        so the model can follow a thread; `prompt` alone is the single-turn form.
        If `stream_q` is given, token chunks are pushed onto it as ("token", text)
        while the job runs; the caller must drain it concurrently.
        """
        with self._cv:
            self._seq += 1
            seq = self._seq
        # Read the balance and take the escrow in ONE atomic step, so two
        # concurrent requests from the same account cannot both price
        # themselves off the same pre-spend balance.
        balance_before, escrow = self.ledger.reserve(
            user, float(max_tokens), f"escrow for job #{seq}")
        priority = -balance_before             # higher balance -> served first
        job = {
            "seq": seq, "priority": priority, "user": user, "prompt": prompt,
            "messages": messages or [{"role": "user", "content": prompt}],
            "submitted_at": time.time(),
            "max_tokens": max_tokens, "escrow": escrow, "stream_q": stream_q,
            "done": threading.Event(), "slot": {}, "cancelled": False, "started": False,
            "streamed": False,
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
        """Pick the next job by accumulated priority, with a per-account
        reserve lane.

        Two properties this has to have, both learned the hard way:

        * **No one waits forever.** Ordering purely by a credit *stock* is
          Cobham (1954) static priority, whose lowest class waits without bound
          while higher-priority work keeps arriving. Priority here therefore
          accumulates with waiting: score = rate x seconds-waited, where rate
          comes from credits. A contributor still overtakes, but a guest's
          score climbs until it wins — bounded, not merely "eventually".
        * **The reserve lane is per account.** Handing every Nth slot to the
          globally-oldest job let a single guest flooding requests own the
          whole lane and starve every other guest — the exact population the
          lane exists to protect. The lane now rotates over *accounts*, so one
          person cannot occupy it.
        """
        now = time.time()
        self._served += 1
        if self._served % self.FIFO_EVERY == 0:
            # Reserve lane: pick the account waiting longest (least recently
            # served), then that account's oldest job.
            def lane_key(j: dict) -> tuple:
                return (self._last_served_at.get(j["user"], 0.0), j["seq"])
            job = min(self._pending, key=lane_key)
        else:
            # rate in (0, 1]: credits raise it, but never to infinity, so the
            # gap between a whale and a guest stays bounded.
            def score(j: dict) -> float:
                credits = max(-j["priority"], 0.0)
                rate = self.GUEST_RATE + (1.0 - self.GUEST_RATE) * (
                    credits / (credits + self.CREDIT_HALF))
                return rate * (now - j["submitted_at"])
            job = max(self._pending, key=score)
        self._pending.remove(job)
        self._last_served_at[job["user"]] = now
        return job

    def _dispatch_loop(self) -> None:
        """Admit jobs up to `concurrency`; each admitted job runs in its own
        thread so several people are served at once (the engine interleaves
        them across its slots with continuous batching)."""
        while True:
            job = None
            try:
                self._slots.acquire()
                with self._cv:
                    while not self._pending:
                        self._cv.wait()
                    job = self._pop_next_locked()
                    # Claim-or-skip must be atomic with the pop: otherwise a
                    # job can be refunded as "timed out" and still run, minting
                    # credits against an escrow that was already returned.
                    cancelled = job["cancelled"]
                    if not cancelled:
                        job["started"] = True
                if cancelled:
                    self._event(f"job #{job['seq']} cancelled before start - "
                                "skipped (escrow refunded)")
                    job["done"].set()
                    self._slots.release()
                    continue
                threading.Thread(target=self._execute, args=(job,), daemon=True).start()
            except Exception as exc:
                # The dispatcher must never die: a dead dispatcher stops all
                # inference while /status still looks healthy.
                self._event(f"WARNING dispatcher error: {type(exc).__name__}: {exc}")
                if job is not None:
                    job["slot"]["error"] = f"{type(exc).__name__}: {exc}"
                    if job["escrow"] > 0:
                        self.ledger.earn(job["user"], job["escrow"],
                                         f"escrow refund (job #{job['seq']} dispatch failed)")
                    job["done"].set()
                self._slots.release()
                time.sleep(0.1)

    def _execute(self, job: dict) -> None:
        try:
            job["slot"]["result"] = self._run_job_with_repair(job)
            if job["stream_q"] is not None:
                job["stream_q"].put(("done", job["slot"]["result"]))
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            job["slot"]["error"] = msg
            if job["escrow"] > 0:
                self.ledger.earn(job["user"], job["escrow"],
                                 f"escrow refund (job #{job['seq']} failed)")
            if job["stream_q"] is not None:
                job["stream_q"].put(("error", msg))
        finally:
            job["done"].set()
            self._slots.release()

    def _run_job_with_repair(self, job: dict) -> dict:
        """On pipeline failure, evict nodes that have genuinely gone silent,
        rebuild the engine once, and retry. Capacity errors fail fast.

        Two properties matter under concurrency: a node that is heartbeating
        normally is never evicted just because one request failed, and a burst
        of simultaneous failures triggers ONE rebuild rather than a storm.
        """
        try:
            return self._run_job(job)
        except ChainFailure as exc:
            if job.get("streamed"):
                # Tokens already reached the user; a retry would append a second
                # answer to the same bubble. Fail honestly instead.
                raise
            now = time.time()
            with self._repair_lock:
                fresh_repair = now - self._last_repair > REPAIR_DEBOUNCE_S
                if fresh_repair:
                    self._last_repair = now
                    silent = [nid for nid in exc.node_ids
                              if self.registry.last_seen(nid) < now - NODE_TTL_S]
                    for nid in silent:
                        self.registry.suspect(nid)
                    self.engines.invalidate()
                    self._event(f"chain failed - evicted {silent or 'no'} silent node(s); "
                                "rebuilding engine once and retrying")
                else:
                    self._event(f"chain failed - a rebuild is already in progress; "
                                "job #{} will retry against it".format(job["seq"]))
            time.sleep(self.RETRY_DELAY_S)
            return self._run_job(job, repaired=True)

    def _run_job(self, job: dict, repaired: bool = False) -> dict:
        tier, nodes, pool_mb = self.current_tier()
        if not nodes:
            raise CapacityError("no live nodes registered - start sanad_net.node first")
        if tier is None:
            raise CapacityError(f"pool of {pool_mb:.0f} MB cannot hold any catalog model")

        job_start_ts = time.time()
        try:
            engine = self.engines.ensure(tier.path, nodes)
            on_token = None
            if job["stream_q"] is not None:
                def on_token(t: str) -> None:
                    job["streamed"] = True     # past this point a retry would double up
                    job["stream_q"].put(("token", t))
            result = engine.chat(job["messages"], job["max_tokens"], on_token=on_token)
        except EngineError as exc:
            failure = ChainFailure(str(exc))
            failure.node_ids = [n["node_id"] for n in nodes]
            failure.job_start_ts = job_start_ts
            raise failure from exc

        # A model can generate tokens that decode to nothing — a broken or
        # unsupported chat template producing only special tokens. The engine
        # already falls back to reasoning content, so a still-empty answer here
        # means the template is genuinely unusable. Flag it: a blank reply is
        # worse than an honest error.
        if result["decode_tokens"] > 3 and not result["text"].strip():
            self._event(f"WARNING model '{tier.name}' produced {result['decode_tokens']} "
                        "tokens but no readable text - its chat template is likely "
                        "unsupported. Try a model with a standard template.")

        # Layer-share weighted credits: memory lent == layers held == share earned.
        tokens = result["decode_tokens"]
        shard_map = engine.shard_map or {}
        known = {f"RPC{i}" for i in range(len(nodes))}
        provable = tokens > 0 and shard_map and set(shard_map) <= known
        if provable:
            total_layers = sum(d["n_layers"] for d in shard_map.values())
            for i, n in enumerate(nodes):   # RPC device order follows the --rpc list order
                n_layers = shard_map.get(f"RPC{i}", {}).get("n_layers", 0)
                share = tokens * (n_layers / total_layers)
                if share > 0:
                    self.ledger.earn(
                        n["operator"], round(share, 3),
                        f"served {n_layers}/{total_layers} layers of {tier.name} "
                        f"for {tokens} tokens via {n['node_id']}",
                    )
            refund = job["escrow"] - float(tokens)
            if refund > 0:
                self.ledger.earn(job["user"], refund,
                                 f"escrow refund (job #{job['seq']} used {tokens} tokens)")
        else:
            if job["escrow"] > 0:
                self.ledger.earn(job["user"], job["escrow"],
                                 f"escrow refund (job #{job['seq']}: shard map unprovable)")
            self._event(f"WARNING job #{job['seq']}: engine log unprovable "
                        f"(tokens={tokens}, devices={sorted(shard_map)}) - "
                        "no credits minted or charged")
        self.jobs_done += 1
        return {
            "user": job["user"],
            "model": tier.name,
            "turns": len(job["messages"]),      # how much conversation was carried
            "priority_at_submit": -job["priority"],
            "repaired": repaired,
            "pool_mb": round(pool_mb, 1),
            "engine_warm": engine.started_at < job_start_ts,
            "shard_map": shard_map,
            "pipeline": [
                {"node_id": n["node_id"], "endpoint": f"{n['host']}:{n['port']}",
                 "operator": n["operator"], "pledge_mb": n["pledge_mb"]}
                for n in nodes
            ],
            **result,
        }

    def status(self) -> dict:
        tier, nodes, pool_mb = self._compute_tier()
        eng = self.engines.engine
        return {
            "model": tier.name if tier else None,
            "pool_mb": round(pool_mb, 1),
            "catalog": [{"name": t.name, "file_mb": round(t.file_mb, 1),
                         "need_mb": round(t.need_mb, 1)} for t in self.catalog],
            "nodes": nodes,
            "engine": {
                "resident": bool(eng and eng.alive()),
                "load_s": eng.load_s if eng else None,
                "restarts": self.engines.restarts,
                "shard_map": eng.shard_map if eng else {},
            },
            "balances": self.ledger.balances(),
            "jobs_done": self.jobs_done,
            "events": list(self.events)[-20:],
        }


def make_handler(coord: Coordinator):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

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
            if self.path in ("/", "/index.html", "/chat"):
                try:
                    body = WEBUI.read_bytes()
                except OSError:
                    self._json(404, {"error": "web UI not installed"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path in ("/v1/models", "/models"):
                self._openai_models()
            elif self.path == "/status":
                self._json(200, coord.status())
            elif self.path == "/ledger/audit":
                self._json(200, coord.ledger.audit())
            elif self.path.startswith("/ledger"):
                self._json(200, {"entries": [
                    {"ts": round(e.ts, 2), "account": e.account, "delta": e.delta, "reason": e.reason}
                    for e in coord.ledger.entries()
                ]})
            else:
                self._json(404, {"error": "unknown path"})

        def _parse_ask(self, data: dict) -> tuple[str, str, int, list[dict]]:
            raw = data.get("max_tokens", 48)
            if isinstance(raw, float) and not math.isfinite(raw):
                raise ValueError("max_tokens must be finite")
            max_tokens = max(1, min(int(raw), MAX_TOKENS_CAP))
            user = str(data.get("user", "anon"))

            messages = data.get("messages")
            if messages is not None:
                if not isinstance(messages, list) or not messages:
                    raise ValueError("messages must be a non-empty list")
                clean = []
                for m in messages:
                    role = str(m.get("role", "user"))
                    if role not in ("system", "user", "assistant"):
                        raise ValueError(f"unknown message role: {role}")
                    content = str(m.get("content", ""))[:MAX_MESSAGE_CHARS]
                    clean.append({"role": role, "content": content})
                if len(clean) > MAX_MESSAGES:
                    # Trim the middle, never the head: a leading system message
                    # sets the rules for the whole conversation, and dropping it
                    # silently changes the model's behavior.
                    head = [m for m in clean[:1] if m["role"] == "system"]
                    tail = clean[len(head):][-(MAX_MESSAGES - len(head)):]
                    clean = head + tail
                if clean[-1]["role"] != "user":
                    raise ValueError("the last message must be from the user")
                prompt = clean[-1]["content"]
                return user, prompt, max_tokens, clean

            prompt = str(data["prompt"])[:MAX_MESSAGE_CHARS]
            return user, prompt, max_tokens, [{"role": "user", "content": prompt}]

        # -- OpenAI-compatible surface -------------------------------------
        # The point of Sanad is that a community can serve itself instead of
        # renting access. That only works if the things people already use —
        # agents, editors, scripts, SDKs — can point at it without being
        # rewritten. So we speak the shape everything already speaks. The
        # credits, the ladder and the fairness rules underneath are unchanged.

        def _openai_models(self) -> None:
            tier, _nodes, _pool = coord._compute_tier()
            now = int(time.time())
            data = [{"id": t.name, "object": "model", "created": now, "owned_by": "sanad",
                     "sanad": {"serving": bool(tier and t.name == tier.name),
                               "needs_pool_mb": round(t.need_mb, 1)}}
                    for t in coord.catalog]
            self._json(200, {"object": "list", "data": data})

        def _openai_chat(self, data: dict) -> None:
            """POST /v1/chat/completions — the endpoint every tool already knows."""
            messages = data.get("messages")
            if not isinstance(messages, list) or not messages:
                raise ValueError("messages must be a non-empty list")
            raw_max = data.get("max_tokens") or data.get("max_completion_tokens") or 256
            if isinstance(raw_max, float) and not math.isfinite(raw_max):
                raise ValueError("max_tokens must be finite")
            max_tokens = max(1, min(int(raw_max), MAX_TOKENS_CAP))
            # OpenAI has no notion of who is contributing, so let a caller say
            # so with the standard "user" field; otherwise they are a guest.
            user = str(data.get("user") or "anon")
            _u, _p, _n, clean = self._parse_ask({"user": user, "messages": messages,
                                                 "max_tokens": max_tokens})
            created = int(time.time())
            cid = f"chatcmpl-sanad-{created}-{abs(hash(str(messages))) % 10**8}"

            if not data.get("stream"):
                result = coord.submit(user, _p, max_tokens, messages=clean)
                self._json(200, {
                    "id": cid, "object": "chat.completion", "created": created,
                    "model": result["model"],
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": result["text"]}}],
                    "usage": {"prompt_tokens": result.get("prompt_tokens", 0),
                              "completion_tokens": result["decode_tokens"],
                              "total_tokens": result.get("prompt_tokens", 0)
                              + result["decode_tokens"]},
                    # Sanad's own accounting, alongside the standard fields —
                    # a caller can ignore it, or show who served them.
                    "sanad": {"pipeline": result["pipeline"], "shard_map": result["shard_map"],
                              "tok_per_s": result["tok_per_s"], "warm": result["engine_warm"]},
                })
                return

            q: queue.Queue = queue.Queue()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

            def run():
                try:
                    coord.submit(user, _p, max_tokens, stream_q=q, messages=clean)
                except Exception as exc:
                    q.put(("error", f"{type(exc).__name__}: {exc}"))

            threading.Thread(target=run, daemon=True).start()

            def send(payload: dict) -> bool:
                try:
                    self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode())
                    self.wfile.flush()
                    return True
                except (BrokenPipeError, ConnectionResetError):
                    return False

            def chunk(delta: dict, finish=None) -> dict:
                return {"id": cid, "object": "chat.completion.chunk", "created": created,
                        "model": coord.status().get("model") or "sanad",
                        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

            if not send(chunk({"role": "assistant", "content": ""})):
                return
            while True:
                kind, payload = q.get()
                if kind == "token":
                    if not send(chunk({"content": payload})):
                        return
                elif kind == "done":
                    send(chunk({}, finish="stop"))
                    try:
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                else:
                    send({"error": {"message": payload, "type": "sanad_error"}})
                    return

        def _stream_ask(self, data: dict) -> None:
            user, prompt, max_tokens, messages = self._parse_ask(data)
            q: queue.Queue = queue.Queue()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

            def run():
                try:
                    coord.submit(user, prompt, max_tokens, stream_q=q, messages=messages)
                except Exception as exc:
                    q.put(("error", f"{type(exc).__name__}: {exc}"))

            threading.Thread(target=run, daemon=True).start()
            while True:
                kind, payload = q.get()
                if kind == "token":
                    ev = {"type": "token", "text": payload}
                elif kind == "done":
                    ev = {"type": "done", "result": payload}
                else:
                    ev = {"type": "error", "error": payload}
                try:
                    self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return          # client closed; the job still completes and settles
                if kind in ("done", "error"):
                    return

        def do_POST(self):
            try:
                data = self._read_body()
                if self.path == "/register":
                    pledge = float(data.get("pledge_mb", 800))
                    # Python's json accepts Infinity/NaN — an infinite pledge would
                    # pin the ladder to the largest tier and NaN would poison math.
                    if not math.isfinite(pledge) or not 0 < pledge <= MAX_PLEDGE_MB:
                        raise ValueError(f"pledge_mb must be finite in (0, {MAX_PLEDGE_MB}]")
                    coord.registry.register(data["node_id"], data["host"], data["port"],
                                            data["operator"], pledge)
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
                    user, prompt, max_tokens, messages = self._parse_ask(data)
                    self._json(200, coord.submit(user, prompt, max_tokens, messages=messages))
                elif self.path == "/ask/stream":
                    self._stream_ask(data)
                elif self.path in ("/v1/chat/completions", "/chat/completions"):
                    self._openai_chat(data)
                else:
                    self._json(404, {"error": "unknown path"})
            except Exception as exc:
                try:
                    self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
                except Exception:
                    pass

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description="Sanad coordinator")
    ap.add_argument("--port", type=int, default=7860)
    ap.add_argument("--bind", default="127.0.0.1",
                    help="bind address; use 0.0.0.0 to accept nodes and clients "
                         "from your local network (v0 has no authentication)")
    ap.add_argument("--models", required=True,
                    help="comma-separated GGUF paths, the capacity-ladder catalog")
    ap.add_argument("--llama-bin", required=True, help="directory containing llama-server")
    ap.add_argument("--engine-port", type=int, default=7970)
    ap.add_argument("--ledger", default=None,
                    help="path to an append-only ledger file; credits survive restarts "
                         "(default: in-memory only)")
    ap.add_argument("--concurrency", type=int, default=4,
                    help="how many requests to serve at once (engine slots)")
    ap.add_argument("--name", default="sanad", help="network name announced on the LAN")
    ap.add_argument("--no-discovery", action="store_true",
                    help="do not answer LAN discovery broadcasts")
    args = ap.parse_args()

    catalog = load_catalog(args.models.split(","), slots=args.concurrency)
    engines = EngineManager(Path(args.llama_bin).resolve(), port=args.engine_port,
                            slots=args.concurrency)
    ledger = Ledger(path=Path(args.ledger)) if args.ledger else Ledger()
    coord = Coordinator(engines, catalog, ledger=ledger, concurrency=args.concurrency)
    if args.bind != "127.0.0.1":
        print("[sanad-coordinator] NOTE: binding beyond loopback. v0 has no "
              "authentication and llama.cpp's RPC backend is not hardened — "
              "trusted networks only.")
    server = ThreadingHTTPServer((args.bind, args.port), make_handler(coord))
    responder = None
    discovery_on = False
    if not args.no_discovery:
        responder = DiscoveryResponder(args.port, name=args.name, bind=args.bind)
        discovery_on = responder.start()
    tiers = " -> ".join(f"{t.name} (needs {t.need_mb:.0f} MB)" for t in catalog)
    print(f"[sanad-coordinator] http://{args.bind}:{args.port}  (open it in a browser to chat)")
    print(f"[sanad-coordinator] ladder: {tiers}")
    print(f"[sanad-coordinator] serving up to {args.concurrency} requests at once"
          + (f"; ledger: {args.ledger}" if args.ledger else "; ledger: in-memory (use --ledger to persist)"))
    if discovery_on:
        print("[sanad-coordinator] nodes on this network can join with: "
              "python -m sanad_net.node --discover ...")
    elif not args.no_discovery:
        print(f"[sanad-coordinator] discovery is off (loopback bind or port {DISCOVERY_PORT} "
              f"in use); nodes must pass --coordinator http://<address>:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if responder is not None:
            responder.stop()
        engines.stop()          # stop serving before closing the books
        ledger.close()


if __name__ == "__main__":
    main()
