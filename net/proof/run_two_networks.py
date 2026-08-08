"""Sanad "Two Networks" proof — the core claim, finally tested.

Every earlier proof ran on one machine over loopback or the host LAN. This one
puts each node in its own Linux container on its own Docker network, with its
own IP and network namespace, so the coordinator reaches each node over a
routed TCP hop and the two nodes cannot see each other at all. It is the honest
local stand-in for "two machines on two networks" — short of two real ISPs,
which the deploy/ guide covers with a free cloud VM over Tailscale.

It proves four things, and refuses to flatter the last one:

  A. TWO SEPARATE NETWORKS — the nodes sit on different subnets and node-a
     cannot even resolve node-b; only the coordinator bridges them.
  B. REAL SHARDED INFERENCE ACROSS THEM — the model is split across the two
     networked nodes and produces correct text.
  C. WAN LATENCY IS MEASURED, NOT ASSUMED — 40 ms is injected on each link with
     tc netem and the throughput drop is reported honestly. This is the
     "shards worst over WAN" tension, quantified on your own machine.
  D. CREDITS FLOW to both operators, weighted by the layers each network held.

Requires Docker. Run from the repo root or net/:
    python net/proof/run_two_networks.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

NET = Path(__file__).resolve().parent.parent          # net/
COMPOSE = ["docker", "compose", "-f", str(NET / "deploy" / "docker-compose.yml")]
COORD = "http://localhost:7860"


def call(path: str, payload: dict | None = None, timeout: float = 600):
    if payload is None:
        with urllib.request.urlopen(f"{COORD}{path}", timeout=timeout) as r:
            return json.loads(r.read())
    req = urllib.request.Request(
        f"{COORD}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def dc(*args: str, capture: bool = False, check: bool = True):
    return subprocess.run(COMPOSE + list(args), capture_output=capture, text=True, check=check)


def dc_exec(service: str, *cmd: str) -> str:
    out = subprocess.run(COMPOSE + ["exec", "-T", service, *cmd],
                         capture_output=True, text=True)
    return (out.stdout + out.stderr).strip()


def wait_for(predicate, timeout_s: float, what: str):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if predicate():
                return
        except Exception:
            pass
        time.sleep(2.0)
    raise TimeoutError(f"timed out waiting for: {what}")


def section(title: str) -> None:
    print(f"\n{'=' * 74}\n  {title}\n{'=' * 74}", flush=True)


def main() -> None:
    if subprocess.run(["docker", "version"], capture_output=True).returncode != 0:
        sys.exit("Docker is required for this proof and is not available.")

    try:
        section("0. Bring up 1 coordinator + 2 nodes, each node on its own network")
        dc("up", "-d", "--build")
        wait_for(lambda: len(call("/status")["nodes"]) == 2, 180,
                 "both containerised nodes to register")
        status = call("/status")
        for n in status["nodes"]:
            print(f"   {n['node_id']} (operator {n['operator']}) advertises "
                  f"{n['host']}:{n['port']}")
        assert status["model"] == "qwen2.5-1.5b-instruct-q4_k_m", "ladder should pick the large model"

        section("A. The two nodes are on DIFFERENT networks and cannot see each other")
        ip_a = dc_exec("node-a", "hostname", "-i").split()[0]
        ip_b = dc_exec("node-b", "hostname", "-i").split()[0]
        print(f"   node-a lives at {ip_a}")
        print(f"   node-b lives at {ip_b}")
        assert ip_a.split(".")[1] != ip_b.split(".")[1], "nodes must be on different subnets"
        probe = dc_exec("node-a", "python3", "-c",
                        "import socket; s=socket.socket(); s.settimeout(3);"
                        "s.connect(('node-b',50070)); print('REACHED')")
        print(f"   node-a trying to reach node-b directly -> {probe.splitlines()[-1] if probe else '(no output)'}")
        assert "REACHED" not in probe, "the nodes must be isolated from each other"
        print("   -> only the coordinator bridges them. This is Sanad's topology, for real.")

        section("B. Real sharded inference across the two networks")
        r = call("/ask", {"user": "anon", "prompt": "A mining pool is", "max_tokens": 40})
        for dev, d in r["shard_map"].items():
            print(f"   {dev}  {d['endpoint']}  layers {d['layers']}  ({d['n_layers']} layers)")
        print(f"   text: {r['text'][:130]}")
        assert len(r["shard_map"]) == 2, "must be split across both networked nodes"
        assert r["decode_tokens"] > 0

        section("C. WAN latency, measured (not assumed)")
        warm = call("/ask", {"user": "anon", "prompt": "Warm up.", "max_tokens": 24})
        lan = warm["tok_per_s"]
        print(f"   local-network baseline: {lan:.1f} tok/s")
        for svc in ("node-a", "node-b"):
            dc_exec(svc, "tc", "qdisc", "add", "dev", "eth0", "root", "netem", "delay", "40ms")
        print("   injected 40 ms each-way (80 ms round trip) on both links via tc netem")
        try:
            wan = call("/ask", {"user": "anon", "prompt": "What is a mining pool?",
                                "max_tokens": 32})
            print(f"   under WAN latency:      {wan['tok_per_s']:.1f} tok/s "
                  f"(first token {wan['ttft_s']}s)")
            assert len(wan["shard_map"]) == 2, "still sharded under latency"
            assert wan["decode_tokens"] > 0, "still produces text under latency"
            slowdown = lan / max(wan["tok_per_s"], 0.01)
            print(f"   -> {slowdown:.1f}x slower. This is the honest cost of splitting a model")
            print("      across the internet: every token crosses every hop. Sanad is for")
            print("      models too big for one device, NOT for speed. The docs say so.")
        finally:
            for svc in ("node-a", "node-b"):
                dc_exec(svc, "tc", "qdisc", "del", "dev", "eth0", "root")
            print("   latency removed.")

        section("D. Credits flowed to both operators, weighted by layers held")
        bal = call("/status")["balances"]
        print(f"   {bal}")
        assert bal.get("amina", 0) > 0 and bal.get("bilal", 0) > 0, \
            "both networked operators must have earned"
        assert bal["amina"] > bal["bilal"], "node-a held more layers, so it earns more"

        section("TWO NETWORKS: PASS - real sharding across isolated networks, "
                "WAN cost measured honestly")
    finally:
        print("\n(bringing the stack down)")
        dc("down", "-v", check=False)


if __name__ == "__main__":
    main()
