"""Sanad "First Light" proof run — reproducible end-to-end demonstration.

Starts a real Sanad network on this machine (coordinator + 2 nodes), then:
  1. proves the model's layers are split across the two nodes over TCP,
  2. runs real inference through the chain for three users,
  3. proves credit accounting works (serving earns, contributors get priority).

Run from the `net/` directory:
    python proof/run_first_light.py --llama-bin ../.local/bin \
        --model ../.local/models/qwen2.5-0.5b-instruct-q4_k_m.gguf

Requirements: llama.cpp Windows binaries (llama-completion.exe + ggml-rpc-server.exe)
and a small GGUF model. See net/README.md for download instructions.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

COORD = "http://127.0.0.1:7861"


def call(path: str, payload: dict | None = None) -> dict:
    if payload is None:
        with urllib.request.urlopen(f"{COORD}{path}", timeout=900) as r:
            return json.loads(r.read())
    req = urllib.request.Request(
        f"{COORD}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read())


def section(title: str) -> None:
    print(f"\n{'=' * 72}\n  {title}\n{'=' * 72}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llama-bin", default="../.local/bin")
    ap.add_argument("--model", default="../.local/models/qwen2.5-0.5b-instruct-q4_k_m.gguf")
    args = ap.parse_args()
    llama_bin = str(Path(args.llama_bin).resolve())
    model = str(Path(args.model).resolve())
    net_dir = Path(__file__).resolve().parent.parent

    procs: list[subprocess.Popen] = []

    def spawn(mod_args: list[str]) -> subprocess.Popen:
        p = subprocess.Popen([sys.executable, "-m", *mod_args], cwd=str(net_dir))
        procs.append(p)
        return p

    try:
        section("1. Starting the Sanad network (1 coordinator + 2 nodes)")
        spawn(["sanad_net.coordinator", "--port", "7861", "--models", model, "--llama-bin", llama_bin])
        time.sleep(1.5)
        spawn(["sanad_net.node", "--node-id", "riyadh-a", "--operator", "amina",
               "--port", "50070", "--coordinator", COORD, "--rpc-bin", llama_bin])
        spawn(["sanad_net.node", "--node-id", "jeddah-b", "--operator", "bilal",
               "--port", "50071", "--coordinator", COORD, "--rpc-bin", llama_bin])

        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                if len(call("/status")["nodes"]) >= 2:
                    break
            except Exception:
                pass
            time.sleep(1.0)
        status = call("/status")
        print(json.dumps(status, indent=2))
        assert len(status["nodes"]) >= 2, "expected 2 registered nodes"

        section("2. Real inference, sharded across both nodes (anonymous user)")
        r1 = call("/ask", {"user": "anon", "prompt": "A mining pool is", "max_tokens": 32})
        print(json.dumps(r1, indent=2, ensure_ascii=False))
        assert r1["decode_tokens"] > 0, "no tokens generated"
        assert len(r1["shard_map"]) >= 2, "model was NOT split across 2 nodes"

        section("3. Credit accounting: serving earned credits for node operators")
        status = call("/status")
        print(json.dumps(status["balances"], indent=2))
        assert status["balances"].get("amina", 0) > 0
        assert status["balances"].get("bilal", 0) > 0

        section("4. Priority: contributor (amina) vs anonymous (zed), submitted after her queue rival")
        results: list[tuple[str, float]] = []
        lock = threading.Lock()

        def ask(user: str, prompt: str) -> None:
            r = call("/ask", {"user": user, "prompt": prompt, "max_tokens": 24})
            with lock:
                results.append((user, r["priority_at_submit"]))

        threads = [threading.Thread(target=ask, args=("anon", "The sun is"))]
        threads[0].start()
        time.sleep(1.0)  # filler job is now running; next two land in the queue
        t_zed = threading.Thread(target=ask, args=("zed", "Water boils at"))
        t_zed.start()
        time.sleep(0.3)  # zed is queued FIRST...
        t_amina = threading.Thread(target=ask, args=("amina", "The moon orbits"))
        t_amina.start()  # ...amina second, but she has credits
        for t in [*threads, t_zed, t_amina]:
            t.join(timeout=900)

        print("completion order (user, priority_at_submit):")
        for user, prio in results:
            print(f"  {user:8s} priority={prio}")
        order = [u for u, _ in results]
        assert order.index("amina") < order.index("zed"), (
            "amina (contributor) should be served before zed (anonymous) "
            "even though zed was queued first"
        )
        print("-> amina was queued AFTER zed but served BEFORE him: credits buy priority.")
        print("-> zed (anonymous, zero credits) was still served: priority, never access.")

        section("5. Final network state")
        print(json.dumps(call("/status"), indent=2))

        section("FIRST LIGHT: PASS - real sharded inference + fair credits, end to end")
    finally:
        for p in procs:
            p.terminate()
        # node.py's SIGTERM handler cannot run under TerminateProcess on Windows,
        # so reap any orphaned rpc-servers explicitly.
        subprocess.run(["taskkill", "/F", "/IM", "ggml-rpc-server.exe"],
                       capture_output=True)


if __name__ == "__main__":
    main()
