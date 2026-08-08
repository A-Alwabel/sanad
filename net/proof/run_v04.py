"""Sanad v0.4 proof — conversation, durability, concurrency, one-command join.

Everything here runs over the machine's real LAN address.

  A. ONE-COMMAND JOIN — a node started with only `--discover` finds the
     coordinator by asking the network, with no address typed anywhere.
  B. IT REMEMBERS — a conversation is carried through the model's own chat
     template, so turn 2 can answer a question that only turn 1 established.
  C. CREDITS SURVIVE — the coordinator is killed and restarted; balances come
     back exactly, and an independent replay of the ledger file agrees.
  D. MANY AT ONCE — several people are served concurrently, not one after
     another.

Run from the `net/` directory:
    python proof/run_v04.py --llama-bin ../.local/bin --models-dir ../.local/models
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

SMALL = "qwen2.5-0.5b-instruct-q4_k_m"
LARGE = "qwen2.5-1.5b-instruct-q4_k_m"
PORT = 7866
ENGINE_PORT = 7976
COORD = ""


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def call(path: str, payload: dict | None = None, timeout: float = 900):
    if payload is None:
        with urllib.request.urlopen(f"{COORD}{path}", timeout=timeout) as r:
            return json.loads(r.read())
    req = urllib.request.Request(
        f"{COORD}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def wait_for(predicate, timeout_s: float, what: str):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if predicate():
                return
        except Exception:
            pass
        time.sleep(1.0)
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
    tmp = tempfile.mkdtemp(prefix="sanad-proof-")
    ledger_path = str(Path(tmp) / "ledger.jsonl")
    procs: list[subprocess.Popen] = []

    def spawn(mod_args: list[str]) -> subprocess.Popen:
        p = subprocess.Popen([sys.executable, "-m", *mod_args], cwd=str(net_dir))
        procs.append(p)
        return p

    def start_coordinator() -> subprocess.Popen:
        return spawn(["sanad_net.coordinator", "--port", str(PORT), "--bind", "0.0.0.0",
                      "--models", models, "--llama-bin", llama_bin,
                      "--engine-port", str(ENGINE_PORT), "--ledger", ledger_path,
                      "--concurrency", "4", "--name", "sanad-proof"])

    try:
        section(f"0. Real network on {ip} (not loopback); ledger at {ledger_path}")
        coord_proc = start_coordinator()
        time.sleep(2)

        section("A. ONE-COMMAND JOIN - the node is given no address at all")
        # Note what is NOT passed: no --coordinator, no IP, no port.
        spawn(["sanad_net.node", "--node-id", "riyadh-a", "--operator", "amina",
               "--port", "50140", "--pledge-mb", "1000", "--busy-at", "101",
               "--discover", "--rpc-bin", llama_bin])
        wait_for(lambda: len(call("/status")["nodes"]) == 1, 90, "node discovering and joining")
        node = call("/status")["nodes"][0]
        print(f"node joined by broadcast: advertises {node['host']}:{node['port']}, "
              f"operator {node['operator']}")
        assert not str(node["host"]).startswith("127."), "node must advertise a LAN address"

        section("B. IT REMEMBERS - turn 2 answers what only turn 1 established")
        convo = [{"role": "user",
                  "content": "My name is Abdullah and my project is called Sanad. "
                             "Please remember both."}]
        r1 = call("/ask", {"user": "anon", "messages": convo, "max_tokens": 64})
        print(f"turn 1 -> {r1['text'][:120]}")
        convo.append({"role": "assistant", "content": r1["text"]})
        convo.append({"role": "user", "content": "What is my name and my project called?"})
        r2 = call("/ask", {"user": "anon", "messages": convo, "max_tokens": 64})
        print(f"turn 2 -> {r2['text'][:160]}")
        low = r2["text"].lower()
        assert "abdullah" in low and "sanad" in low, \
            "the model did not carry the conversation forward"
        print("-> both facts recalled: the conversation is real, not a series of strangers")

        section("D. MANY AT ONCE - four people served concurrently")
        durations: dict[int, float] = {}

        def ask_timed(i: int, user: str, prompt: str) -> None:
            t0 = time.time()
            call("/ask", {"user": user, "prompt": prompt, "max_tokens": 48})
            durations[i] = time.time() - t0

        people = [("ali", "Name a color."), ("noura", "Name a city."),
                  ("sara", "Name an animal."), ("omar", "Name a fruit.")]
        t0 = time.time()
        threads = [threading.Thread(target=ask_timed, args=(i, u, p))
                   for i, (u, p) in enumerate(people)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=600)
        wall = time.time() - t0
        serial = sum(durations.values())
        print(f"individual durations: {[round(durations[i], 2) for i in sorted(durations)]}")
        print(f"wall clock for all four: {wall:.2f}s   (sum if served one-by-one: {serial:.2f}s)")
        assert len(durations) == 4, "every request must complete"
        assert wall < serial * 0.8, "requests were serialized, not concurrent"
        print(f"-> {serial / wall:.1f}x more throughput than serving them one at a time")

        section("C. CREDITS SURVIVE - kill the coordinator, bring it back")
        before = call("/status")["balances"]
        print(f"balances before: {before}")
        assert before.get("amina", 0) > 0, "the node operator should have earned by now"

        coord_proc.terminate()
        coord_proc.wait(timeout=30)
        subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True)
        print("coordinator killed.")

        # Independent check: replay the ledger file without the coordinator.
        sys.path.insert(0, str(net_dir))
        from sanad_net.ledger import Ledger  # noqa: E402
        replay = Ledger(path=Path(ledger_path))
        replayed = replay.balances()
        audit = replay.audit()
        replay.close()
        print(f"replayed from the file alone: {replayed}  (entries: {audit['entries']}, "
              f"self-consistent: {audit['consistent']})")
        assert audit["consistent"], "the ledger file must be internally consistent"
        for account, amount in before.items():
            assert abs(replayed.get(account, 0.0) - amount) < 1e-6, \
                f"{account}: file says {replayed.get(account)}, coordinator said {amount}"

        time.sleep(2)
        start_coordinator()
        wait_for(lambda: call("/status") is not None, 60, "coordinator restarting")
        after = call("/status")["balances"]
        print(f"balances after restart: {after}")
        for account, amount in before.items():
            assert abs(after.get(account, 0.0) - amount) < 1e-6, \
                f"{account} lost credits across the restart"
        print("-> every credit came back. Contribution is not erased by an outage.")

        section("V0.4: PASS - one-command join, memory, durable credits, concurrency")
    finally:
        for p in procs:
            p.terminate()
        for image in ("ggml-rpc-server.exe", "llama-server.exe"):
            subprocess.run(["taskkill", "/F", "/IM", image], capture_output=True)


if __name__ == "__main__":
    main()
