"""Sanad "Living Network" proof run (v0.2) — reproducible end-to-end demonstration.

Proves, on one machine, the three v0.2 behaviors on top of First Light:

  A. CAPACITY LADDER — with one node the network serves the small model; when a
     second node joins, it automatically upgrades to the larger model; when a
     node leaves, it downgrades — and never stops serving.
  B. THE POLITE NODE — a node whose owner starts heavy work (simulated by a
     CPU hog at normal priority) drains out gracefully on its own, returns all
     memory, and rejoins automatically once the machine is calm again.
  C. WEIGHTED FAIRNESS — credits per token are split by the layer share each
     node actually held, which follows its memory pledge (1000 MB vs 700 MB),
     and balances survive leaving: withdrawal is never punished.

Run from the `net/` directory:
    python proof/run_living_network.py --llama-bin ../.local/bin --models-dir ../.local/models
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

COORD = "http://127.0.0.1:7862"
SMALL = "qwen2.5-0.5b-instruct-q4_k_m"
LARGE = "qwen2.5-1.5b-instruct-q4_k_m"

# One spinning process per core: Python threads share the GIL and can only
# burn ~one core total, so a convincing "game" needs separate processes.
HOG_PROCS = None  # set in main from cpu_count


def start_hog() -> list[subprocess.Popen]:
    import os
    n = os.cpu_count() or 4
    return [subprocess.Popen([sys.executable, "-c", "while True: pass"])
            for _ in range(n)]


def stop_hog(hogs: list[subprocess.Popen]) -> None:
    for h in hogs:
        h.terminate()


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
    print(f"\n{'=' * 72}\n  {title}\n{'=' * 72}", flush=True)


def brief(r: dict) -> dict:
    return {k: r[k] for k in ("user", "model", "text", "tok_per_s", "shard_map", "repaired") if k in r}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llama-bin", default="../.local/bin")
    ap.add_argument("--models-dir", default="../.local/models")
    args = ap.parse_args()
    llama_bin = str(Path(args.llama_bin).resolve())
    models_dir = Path(args.models_dir).resolve()
    models = f"{models_dir / (SMALL + '.gguf')},{models_dir / (LARGE + '.gguf')}"
    net_dir = Path(__file__).resolve().parent.parent

    procs: list[subprocess.Popen] = []
    hogs: list[subprocess.Popen] = []

    def spawn(mod_args: list[str]) -> subprocess.Popen:
        p = subprocess.Popen([sys.executable, "-m", *mod_args], cwd=str(net_dir))
        procs.append(p)
        return p

    try:
        section("1. Coordinator up with a 2-tier catalog; dedicated node A joins (pledge 1000 MB)")
        spawn(["sanad_net.coordinator", "--port", "7862", "--models", models, "--llama-bin", llama_bin, "--engine-port", "7972"])
        time.sleep(1.5)
        spawn(["sanad_net.node", "--node-id", "riyadh-a", "--operator", "amina",
               "--port", "50090", "--pledge-mb", "1000", "--busy-at", "101",
               "--coordinator", COORD, "--rpc-bin", llama_bin])
        wait_for(lambda: len(call("/status")["nodes"]) == 1, 60, "node A registration")
        st = call("/status")
        print(json.dumps({k: st[k] for k in ("model", "pool_mb", "catalog")}, indent=2))
        assert st["model"] == SMALL, f"expected {SMALL}, got {st['model']}"

        section("2. Ask with one node -> served by the SMALL model")
        r = call("/ask", {"user": "anon", "prompt": "The sea is", "max_tokens": 24})
        print(json.dumps(brief(r), indent=2, ensure_ascii=False))
        assert r["model"] == SMALL

        section("3. LADDER UP: polite node B joins (pledge 700 MB) -> pool fits the LARGE model")
        # busy-at 85: on this single-machine proof the resident engine's own
        # load counts as "other process" from the node's point of view, so the
        # threshold must sit above ordinary inference and below a saturated
        # machine. On separate machines the engine is not a neighbour at all.
        spawn(["sanad_net.node", "--node-id", "jeddah-b", "--operator", "bilal",
               "--port", "50091", "--pledge-mb", "700", "--busy-at", "85", "--resume-at", "45",
               "--coordinator", COORD, "--rpc-bin", llama_bin])
        wait_for(lambda: call("/status")["model"] == LARGE, 60, "ladder upgrade to large model")
        r = call("/ask", {"user": "anon", "prompt": "A mining pool is", "max_tokens": 24})
        print(json.dumps(brief(r), indent=2, ensure_ascii=False))
        assert r["model"] == LARGE
        assert len(r["shard_map"]) == 2, "large model should be sharded across both nodes"

        section("4. WEIGHTED FAIRNESS: credits follow the memory pledge (1000 vs 700)")
        bal = call("/status")["balances"]
        print(json.dumps(bal, indent=2))
        assert bal["amina"] > bal["bilal"] > 0, "bigger pledge must earn the bigger share"
        bilal_before_leaving = bal["bilal"]

        section("5. THE POLITE NODE: owner starts heavy work -> node B drains out on its own")
        hogs = start_hog()
        print(f"(cpu hog: {len(hogs)} spinner processes at NORMAL priority - simulating the owner's game)")
        wait_for(lambda: len(call("/status")["nodes"]) == 1, 90, "node B draining out under load")
        st = call("/status")
        print(f"nodes now: {[n['node_id'] for n in st['nodes']]}  model: {st['model']}")
        assert st["model"] == SMALL, "ladder must step DOWN when the pool shrinks"

        section("6. Network keeps serving DURING the owner's heavy work (slower, but alive)")
        r = call("/ask", {"user": "anon", "prompt": "Rain falls when", "max_tokens": 12})
        print(json.dumps(brief(r), indent=2, ensure_ascii=False))
        assert r["model"] == SMALL
        assert len(r["shard_map"]) == 1, "must be served by the remaining node alone"

        section("7. Owner finishes -> machine calm -> node B rejoins BY ITSELF -> LADDER UP again")
        stop_hog(hogs)
        hogs = []
        print("(cpu hog terminated)")
        wait_for(lambda: len(call("/status")["nodes"]) == 2 and call("/status")["model"] == LARGE,
                 120, "node B rejoining and ladder upgrade")
        r = call("/ask", {"user": "anon", "prompt": "Stars shine because", "max_tokens": 24})
        print(json.dumps(brief(r), indent=2, ensure_ascii=False))
        assert r["model"] == LARGE

        section("8. WITHDRAWAL IS NEVER PUNISHED: bilal's wallet statement")
        entries = [e for e in call("/ledger")["entries"] if e["account"] == "bilal"]
        for e in entries:
            print(f"  {e['delta']:+9.3f}  {e['reason']}")
        bal = call("/status")["balances"]
        assert bal["bilal"] >= bilal_before_leaving, "credits must survive leaving"
        print(f"  balance before leaving: {bilal_before_leaving}  after rejoining: {bal['bilal']}")

        section("9. The ladder's own event log")
        for e in call("/status")["events"]:
            print(f"  {e['event']}")

        section("LIVING NETWORK: PASS - ladder + polite node + weighted fairness, end to end")
    finally:
        stop_hog(hogs)
        for p in procs:
            p.terminate()
        for image in ("ggml-rpc-server.exe", "llama-server.exe"):
            subprocess.run(["taskkill", "/F", "/IM", image], capture_output=True)


if __name__ == "__main__":
    main()
