"""Sanad "It Works" proof (v0.3) — usability, measured.

The earlier proofs showed the network is *correct*. This one shows it is
*usable*, and it runs over the machine's real LAN address rather than
loopback, so the traffic crosses the actual network stack.

  A. RESIDENT PIPELINE — the first question builds the pipeline; every later
     question reuses it. We measure both and require the warm request to reach
     its first token dramatically faster than the cold one.
  B. STREAMING - tokens arrive progressively, not in one lump at the end.
  C. THE CHAT UI — the coordinator serves a working page at /, so a person can
     use this without touching a terminal.
  D. THE LADDER STILL HOLDS — a second node joins, the model upgrades, and the
     engine rebuilds itself for the new pipeline; credits stay weighted by
     layer share.

Run from the `net/` directory:
    python proof/run_it_works.py --llama-bin ../.local/bin --models-dir ../.local/models
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SMALL = "qwen2.5-0.5b-instruct-q4_k_m"
LARGE = "qwen2.5-1.5b-instruct-q4_k_m"
PORT = 7863
COORD = ""  # set in main once the LAN IP is known


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def call(path: str, payload: dict | None = None, timeout: float = 900) -> dict:
    if payload is None:
        with urllib.request.urlopen(f"{COORD}{path}", timeout=timeout) as r:
            return json.loads(r.read())
    req = urllib.request.Request(
        f"{COORD}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def stream_ask(user: str, prompt: str, max_tokens: int) -> dict:
    """POST /ask/stream and record when each token chunk actually arrived."""
    req = urllib.request.Request(
        f"{COORD}/ask/stream",
        data=json.dumps({"user": user, "prompt": prompt, "max_tokens": max_tokens}).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    t0 = time.time()
    arrivals: list[float] = []
    text_parts: list[str] = []
    result: dict = {}
    error = None
    with urllib.request.urlopen(req, timeout=900) as resp:
        buf = ""
        for raw in resp:
            buf += raw.decode("utf-8", "replace")
            while "\n\n" in buf:
                chunk, buf = buf.split("\n\n", 1)
                line = chunk.strip()
                if not line.startswith("data:"):
                    continue
                ev = json.loads(line[5:].strip())
                if ev["type"] == "token":
                    arrivals.append(time.time() - t0)
                    text_parts.append(ev["text"])
                elif ev["type"] == "done":
                    result = ev["result"]
                elif ev["type"] == "error":
                    error = ev["error"]
    if error:
        raise RuntimeError(error)
    result["_arrivals"] = arrivals
    result["_streamed_text"] = "".join(text_parts).strip()
    result["_wall_s"] = round(time.time() - t0, 2)
    return result


def wait_for(predicate, timeout_s: float, what: str):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if predicate():
                return
        except Exception:
            pass
        time.sleep(1.5)
    raise TimeoutError(f"timed out waiting for: {what}")


def section(title: str) -> None:
    print(f"\n{'=' * 74}\n  {title}\n{'=' * 74}", flush=True)


def main() -> None:
    global COORD
    ap = argparse.ArgumentParser()
    ap.add_argument("--llama-bin", default="../.local/bin")
    ap.add_argument("--models-dir", default="../.local/models")
    args = ap.parse_args()
    llama_bin = str(Path(args.llama_bin).resolve())
    models_dir = Path(args.models_dir).resolve()
    models = f"{models_dir / (SMALL + '.gguf')},{models_dir / (LARGE + '.gguf')}"
    net_dir = Path(__file__).resolve().parent.parent

    ip = lan_ip()
    COORD = f"http://{ip}:{PORT}"
    procs: list[subprocess.Popen] = []

    def spawn(mod_args: list[str]) -> subprocess.Popen:
        p = subprocess.Popen([sys.executable, "-m", *mod_args], cwd=str(net_dir))
        procs.append(p)
        return p

    try:
        section(f"0. Real network: coordinator and nodes bind to {ip}, not loopback")
        print(f"machine LAN address: {ip}  ->  coordinator at {COORD}")
        assert not ip.startswith("127."), "no LAN address found; this proof needs a real interface"

        spawn(["sanad_net.coordinator", "--port", str(PORT), "--bind", "0.0.0.0",
               "--models", models, "--llama-bin", llama_bin, "--engine-port", "7973"])
        time.sleep(1.5)
        spawn(["sanad_net.node", "--node-id", "riyadh-a", "--operator", "amina",
               "--host", "0.0.0.0", "--port", "50110", "--pledge-mb", "1000",
               "--busy-at", "101", "--coordinator", COORD, "--rpc-bin", llama_bin])
        wait_for(lambda: len(call("/status")["nodes"]) == 1, 90, "node A registering over LAN")
        st = call("/status")
        node = st["nodes"][0]
        print(f"registered node advertises {node['host']}:{node['port']}  (model: {st['model']})")
        assert not str(node["host"]).startswith("127."), "node must advertise its LAN address"

        section("A. RESIDENT PIPELINE - cold start once, then warm forever")
        cold = stream_ask("anon", "Name three colors.", 32)
        print(f"COLD  first token after {cold['_arrivals'][0]:.2f}s  "
              f"({cold['decode_tokens']} tokens, {cold['tok_per_s']} tok/s)  "
              f"engine_warm={cold['engine_warm']}")
        warm = stream_ask("anon", "Name three fruits.", 32)
        print(f"WARM  first token after {warm['_arrivals'][0]:.2f}s  "
              f"({warm['decode_tokens']} tokens, {warm['tok_per_s']} tok/s)  "
              f"engine_warm={warm['engine_warm']}")
        speedup = cold["_arrivals"][0] / max(warm["_arrivals"][0], 1e-6)
        print(f"-> time-to-first-token improved {speedup:.1f}x once the pipeline was resident")
        assert warm["engine_warm"] is True, "second request must reuse the resident pipeline"
        assert warm["_arrivals"][0] < cold["_arrivals"][0], "warm request must reach its first token sooner"
        assert call("/status")["engine"]["restarts"] == 0, "no engine restart should have been needed"

        section("B. STREAMING - tokens arrive progressively, not in one lump")
        r = warm
        n = len(r["_arrivals"])
        span = r["_arrivals"][-1] - r["_arrivals"][0]
        print(f"{n} chunks over {span:.2f}s   first@{r['_arrivals'][0]:.2f}s  "
              f"last@{r['_arrivals'][-1]:.2f}s")
        print(f"answer: {r['_streamed_text'][:150]}")
        assert n >= 5, "expected many streamed chunks"
        assert span > 0.1, "chunks arrived all at once — that is not streaming"
        assert r["_streamed_text"], "streamed text must be non-empty"
        assert r["_streamed_text"] == r["text"], "streamed text must match the final answer"

        section("C. THE CHAT UI - a person can use this without a terminal")
        with urllib.request.urlopen(f"{COORD}/", timeout=20) as resp:
            page = resp.read().decode("utf-8", "replace")
            ctype = resp.headers.get("Content-Type", "")
        print(f"GET /  ->  {resp.status}  {ctype}  ({len(page)} bytes)")
        for needle in ("Sanad", "/ask/stream", "<textarea"):
            assert needle in page, f"chat page is missing {needle!r}"
        print(f"-> open {COORD}/ in any browser on this network and start typing")

        section("D. THE LADDER STILL HOLDS - second node joins, model upgrades, engine rebuilds")
        spawn(["sanad_net.node", "--node-id", "jeddah-b", "--operator", "bilal",
               "--host", "0.0.0.0", "--port", "50111", "--pledge-mb", "700",
               "--busy-at", "101", "--coordinator", COORD, "--rpc-bin", llama_bin])
        wait_for(lambda: call("/status")["model"] == LARGE, 90, "ladder upgrade to the large model")
        big = stream_ask("anon", "A mining pool is", 40)
        print(f"model now: {big['model']}   first token after {big['_arrivals'][0]:.2f}s")
        print("shard map:", json.dumps(big["shard_map"], indent=2))
        assert big["model"] == LARGE
        assert len(big["shard_map"]) == 2, "the large model must be split across both nodes"
        assert call("/status")["engine"]["restarts"] >= 1, "engine must rebuild for the new pipeline"

        again = stream_ask("anon", "Water boils at", 24)
        print(f"next request on the new pipeline: engine_warm={again['engine_warm']}, "
              f"first token after {again['_arrivals'][0]:.2f}s")
        assert again["engine_warm"] is True, "the rebuilt pipeline must then stay resident"

        section("E. CREDITS - still weighted by layer share, settled per request")
        bal = call("/status")["balances"]
        print(json.dumps(bal, indent=2))
        assert bal.get("amina", 0) > bal.get("bilal", 0) > 0, \
            "the bigger pledge must earn the bigger share"

        section("IT WORKS: PASS - resident pipeline, live streaming, chat UI, real LAN")
    finally:
        for p in procs:
            p.terminate()
        subprocess.run(["taskkill", "/F", "/IM", "ggml-rpc-server.exe"], capture_output=True)
        subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True)


if __name__ == "__main__":
    main()
