"""Resident inference engine — the thing that makes Sanad usable.

v0.2 spawned a fresh `llama-completion` per request, which meant re-streaming
the model to every node on every question (~5-6 s of dead time before a single
token). This wraps `llama-server` instead: it stays alive, holds the sharded
pipeline in the nodes' memory, and answers with tokens streaming as they are
produced.

The engine is bound to a specific pipeline — one (model tier, ordered node set)
pair. When the capacity ladder moves or membership changes, the coordinator
stops this engine and starts another. Everything else reuses it.

The shard map (which node holds which layers) is captured once from the
server's start-up log, because it is a property of the pipeline, not of a
request.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

EXE = ".exe" if os.name == "nt" else ""
START_TIMEOUT_S = 300.0     # model must stream to every node before it is ready
HEALTH_POLL_S = 0.5


class EngineError(RuntimeError):
    """The engine failed to start, or died mid-request."""


def parse_shard_map(log_text: str) -> dict:
    """Extract the per-node layer assignment from llama.cpp's verbose log.

    Format (llama.cpp b10276):
      llama_prepare_model_devices: using device RPC0 (127.0.0.1:50060) ...
      load_tensors: layer   5 assigned to device RPC0, is_swa = 0

    Layer assignments are logged once per load pass (2 passes observed); the
    LAST assignment for a layer is its final placement.
    """
    devices: dict[str, str] = {}
    final: dict[int, str] = {}
    for ln in log_text.splitlines():
        m = re.search(r"using device (RPC\d+) \(([^)]+)\)", ln)
        if m:
            devices[m.group(1)] = m.group(2)
            continue
        m = re.search(r"load_tensors: layer\s+(\d+) assigned to device (\S+?),", ln)
        if m:
            final[int(m.group(1))] = m.group(2)
    layers: dict[str, list[int]] = {}
    for layer, dev in final.items():
        layers.setdefault(dev, []).append(layer)
    return {
        dev: {
            "endpoint": devices.get(dev, "?"),
            "layers": f"{min(idx)}-{max(idx)}",
            "n_layers": len(idx),
        }
        for dev, idx in sorted(layers.items())
    }


class Engine:
    """One resident llama-server bound to one pipeline."""

    def __init__(self, bin_dir: Path, model_path: Path, rpc_servers: list[str],
                 tensor_split: list[float], port: int, ctx_size: int = 4096) -> None:
        self.bin_dir = bin_dir
        self.model_path = model_path
        self.rpc_servers = list(rpc_servers)
        self.tensor_split = list(tensor_split)
        self.port = port
        self.ctx_size = ctx_size
        self.base = f"http://127.0.0.1:{port}"
        self.proc: subprocess.Popen | None = None
        self.shard_map: dict = {}
        self.started_at: float = 0.0
        self.load_s: float = 0.0
        self._log: list[str] = []
        self._log_lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        exe = self.bin_dir / f"llama-server{EXE}"
        if not exe.exists():
            raise EngineError(f"llama-server not found at {exe}")
        cmd = [
            str(exe),
            "-m", str(self.model_path),
            "--host", "127.0.0.1",          # engine is coordinator-local by design
            "--port", str(self.port),
            "-ngl", "99",
            "-c", str(self.ctx_size),
            "--no-webui",                    # Sanad serves its own UI
            "-v",                            # exposes per-layer device assignment
        ]
        if self.rpc_servers:
            cmd += ["--rpc", ",".join(self.rpc_servers)]
            if self.tensor_split:
                cmd += ["--tensor-split", ",".join(f"{s:g}" for s in self.tensor_split)]
        t0 = time.time()
        self.proc = subprocess.Popen(
            cmd, cwd=str(self.bin_dir),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        threading.Thread(target=self._drain_log, daemon=True).start()
        self._await_health()
        self.load_s = round(time.time() - t0, 2)
        self.started_at = time.time()
        self.shard_map = parse_shard_map(self.log_text())

    def _drain_log(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        for line in self.proc.stderr:
            with self._log_lock:
                self._log.append(line)
                if len(self._log) > 4000:      # bounded: keep the load lines + recent tail
                    del self._log[1500:2500]

    def log_text(self) -> str:
        with self._log_lock:
            return "".join(self._log)

    def _await_health(self) -> None:
        deadline = time.time() + START_TIMEOUT_S
        while time.time() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                raise EngineError(
                    f"llama-server exited during startup (rc={self.proc.returncode}): "
                    f"{self.log_text()[-600:]}"
                )
            try:
                with urllib.request.urlopen(f"{self.base}/health", timeout=3) as r:
                    if r.status == 200:
                        return
            except Exception:
                pass
            time.sleep(HEALTH_POLL_S)
        self.stop()
        raise EngineError(f"llama-server did not become healthy within {START_TIMEOUT_S:.0f}s")

    def stop(self) -> None:
        if self.proc is not None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=10)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # -- inference -----------------------------------------------------------
    def complete(self, prompt: str, max_tokens: int, on_token=None) -> dict:
        """Stream a completion. `on_token(text)` is called per chunk as it arrives.

        Returns {"text", "decode_tokens", "tok_per_s", "wall_s", "ttft_s"}.
        """
        if not self.alive():
            raise EngineError("engine is not running")
        payload = json.dumps({
            "prompt": prompt,
            "n_predict": max_tokens,
            "temperature": 0,
            "seed": 42,
            "cache_prompt": True,
            "stream": True,
        }).encode()
        req = urllib.request.Request(
            f"{self.base}/completion", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        t0 = time.time()
        ttft = 0.0
        pieces: list[str] = []
        timings: dict = {}
        tokens_predicted = 0
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    chunk = json.loads(line[5:].strip())
                    piece = chunk.get("content", "")
                    if piece:
                        if not pieces:
                            ttft = time.time() - t0
                        pieces.append(piece)
                        if on_token:
                            on_token(piece)
                    if chunk.get("stop"):
                        timings = chunk.get("timings", {}) or {}
                        tokens_predicted = int(chunk.get("tokens_predicted", 0) or 0)
        except urllib.error.URLError as exc:
            raise EngineError(f"engine request failed: {exc}") from exc

        wall = time.time() - t0
        decode_tokens = tokens_predicted or int(timings.get("predicted_n", 0) or 0)
        tok_per_s = float(timings.get("predicted_per_second", 0.0) or 0.0)
        if not tok_per_s and decode_tokens and wall > 0:
            tok_per_s = decode_tokens / wall
        return {
            "text": "".join(pieces).strip(),
            "decode_tokens": decode_tokens,
            "tok_per_s": round(tok_per_s, 2),
            "wall_s": round(wall, 2),
            "ttft_s": round(ttft, 2),
        }


class EngineManager:
    """Keeps exactly one Engine alive for the current pipeline.

    `ensure(model_path, nodes)` returns a ready engine, restarting only when the
    pipeline signature actually changed. Restarts are what make the capacity
    ladder work; everything else is a warm hit.
    """

    def __init__(self, bin_dir: Path, port: int = 7970, on_event=None) -> None:
        self.bin_dir = bin_dir
        self.port = port
        self.on_event = on_event or (lambda msg: None)
        self.engine: Engine | None = None
        self.signature: tuple | None = None
        self.restarts = 0
        self._lock = threading.Lock()

    @staticmethod
    def _signature(model_path: Path, nodes: list[dict]) -> tuple:
        return (str(model_path),
                tuple((n["node_id"], n["host"], n["port"], n["pledge_mb"]) for n in nodes))

    def ensure(self, model_path: Path, nodes: list[dict]) -> Engine:
        sig = self._signature(model_path, nodes)
        with self._lock:
            if self.engine is not None and self.engine.alive() and self.signature == sig:
                return self.engine                      # warm: no reload, no wait
            if self.engine is not None:
                self.engine.stop()
                self.restarts += 1
            eng = Engine(
                self.bin_dir, model_path,
                [f"{n['host']}:{n['port']}" for n in nodes],
                [float(n["pledge_mb"]) for n in nodes],
                self.port,
            )
            self.on_event(f"engine starting: {model_path.stem} across "
                          f"{len(nodes)} node(s) [{', '.join(n['node_id'] for n in nodes)}]")
            eng.start()
            self.on_event(f"engine ready in {eng.load_s}s — pipeline resident, "
                          f"further requests need no reload")
            self.engine = eng
            self.signature = sig
            return eng

    def invalidate(self) -> None:
        """Force a restart on the next ensure() — used when a request fails."""
        with self._lock:
            self.signature = None

    def stop(self) -> None:
        with self._lock:
            if self.engine is not None:
                self.engine.stop()
                self.engine = None
                self.signature = None
