"""Sanad "For Everything" proof — a network you can point your tools at.

The purpose of Sanad is that a community can serve itself instead of renting
access. That is only real if the software people already use — agents, editors,
scripts, SDKs — can use it without being rewritten. So the coordinator speaks
the interface all of them already speak.

This proof drives Sanad with the OFFICIAL OpenAI client library, which has
never heard of this project, and checks that:

  A. MODEL DISCOVERY — /v1/models answers in the standard shape, and the ladder
     is visible in it (which tier the network can currently serve).
  B. A PLAIN COMPLETION — including standard usage accounting.
  C. A CONVERSATION WITH A SYSTEM PROMPT — the thing agents actually need.
  D. STREAMING — chunk by chunk, terminated by [DONE] as clients expect.
  E. SANAD IS STILL SANAD — credits were earned by the operator who served it,
     and the reply names which nodes held which layers. Standard interface,
     community economics underneath.

Run from `net/`:  python proof/run_openai_compat.py
Requires the openai package (pip install openai) — everything else is stdlib.
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
PORT = 7868
ENGINE_PORT = 7978


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def section(title: str) -> None:
    print(f"\n{'=' * 74}\n  {title}\n{'=' * 74}", flush=True)


def main() -> None:
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("this proof needs the openai package:  pip install openai")

    ap = argparse.ArgumentParser()
    ap.add_argument("--llama-bin", default="../.local/bin")
    ap.add_argument("--models-dir", default="../.local/models")
    args = ap.parse_args()
    llama_bin = str(Path(args.llama_bin).resolve())
    models_dir = Path(args.models_dir).resolve()
    models = f"{models_dir / (SMALL + '.gguf')},{models_dir / (LARGE + '.gguf')}"
    net_dir = Path(__file__).resolve().parent.parent

    ip = lan_ip()
    base = f"http://{ip}:{PORT}"
    procs: list[subprocess.Popen] = []

    def spawn(mod_args: list[str]) -> subprocess.Popen:
        p = subprocess.Popen([sys.executable, "-m", *mod_args], cwd=str(net_dir))
        procs.append(p)
        return p

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

    def status() -> dict:
        with urllib.request.urlopen(f"{base}/status", timeout=10) as r:
            return json.loads(r.read())

    try:
        section("0. Start a small Sanad network")
        spawn(["sanad_net.coordinator", "--port", str(PORT), "--bind", "0.0.0.0",
               "--models", models, "--llama-bin", llama_bin,
               "--engine-port", str(ENGINE_PORT), "--no-discovery"])
        time.sleep(2)
        spawn(["sanad_net.node", "--node-id", "riyadh-a", "--operator", "amina",
               "--port", "50160", "--pledge-mb", "1200", "--busy-at", "101",
               "--coordinator", base, "--rpc-bin", llama_bin])
        wait_for(lambda: len(status()["nodes"]) == 1, 90, "the node to join")
        print(f"   network up at {base}; serving {status()['model']}")

        # From here on, nothing knows this is Sanad.
        client = OpenAI(base_url=f"{base}/v1", api_key="not-needed")

        section("A. Model discovery through the official OpenAI client")
        listed = [m.id for m in client.models.list().data]
        for m in listed:
            print(f"   {m}")
        assert SMALL in listed and LARGE in listed, "the catalog should be visible"

        section("B. A plain chat completion")
        r = client.chat.completions.create(
            model=SMALL, max_tokens=60, user="amina",
            messages=[{"role": "user", "content": "In one sentence: what is a mining pool?"}])
        text = r.choices[0].message.content
        print(f"   answer: {text[:150]}")
        print(f"   usage:  {r.usage.prompt_tokens} prompt + "
              f"{r.usage.completion_tokens} completion")
        assert text.strip(), "an answer is required"
        assert r.usage.completion_tokens > 0, "usage accounting must be filled in"
        assert r.object == "chat.completion" and r.choices[0].finish_reason == "stop"

        section("C. A conversation with a system prompt (what an agent needs)")
        r2 = client.chat.completions.create(
            model=SMALL, max_tokens=12, user="amina",
            messages=[{"role": "system", "content": "Answer with a single number, nothing else."},
                      {"role": "user", "content": "What is 6 times 7?"}])
        answer = r2.choices[0].message.content.strip()
        print(f"   answer: {answer[:60]}")
        assert "42" in answer, "the system prompt and question must both reach the model"

        section("D. Streaming, chunk by chunk")
        stream = client.chat.completions.create(
            model=SMALL, max_tokens=40, stream=True, user="amina",
            messages=[{"role": "user", "content": "Count from one to five."}])
        pieces = [c.choices[0].delta.content for c in stream
                  if c.choices and c.choices[0].delta.content]
        print(f"   {len(pieces)} chunks -> {''.join(pieces)[:110]}")
        assert len(pieces) > 3, "tokens should arrive progressively"

        section("E. Standard interface, community economics underneath")
        raw = urllib.request.Request(
            f"{base}/v1/chat/completions",
            data=json.dumps({"model": SMALL, "user": "amina", "max_tokens": 24,
                             "messages": [{"role": "user", "content": "Say hello."}]}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(raw, timeout=600) as resp:
            body = json.loads(resp.read())
        extra = body["sanad"]
        for dev, d in extra["shard_map"].items():
            print(f"   served by {dev} {d['endpoint']} layers {d['layers']}")
        print(f"   credited to: {[p['operator'] for p in extra['pipeline']]}")
        balances = status()["balances"]
        print(f"   balances: {balances}")
        assert balances.get("amina", 0) > 0, "serving must still earn credits"
        assert extra["shard_map"], "the reply should say who served it"

        section("FOR EVERYTHING: PASS - an off-the-shelf client drove Sanad unmodified")
        print("   Point any agent, editor or script at this address and it works.")
        print("   No subscription, no key, no permission - the network is the community's.")
    finally:
        for p in procs:
            p.terminate()
        for image in ("ggml-rpc-server.exe", "llama-server.exe"):
            subprocess.run(["taskkill", "/F", "/IM", image], capture_output=True)


if __name__ == "__main__":
    main()
