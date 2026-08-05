# Sanad v0 prototype — network simulation

A small, runnable **simulation** of the Sanad network semantics: Petals-style
pipeline sharding joined with an AI-Horde-style non-transferable credit
ledger under one credit-priority scheduler.

It uses **mock computation** — no real LLM inference, no networking, no
external dependencies. Python 3.12 standard library only.

## Why a simulation first?

Sanad's contribution is not a new inference engine. Excellent sharded
runtimes already exist — Petals ([arXiv 2312.08361](https://arxiv.org/abs/2312.08361))
and its Hivemind substrate, Parallax (GradientHQ), BloomBee (UC Merced),
prima.cpp ([arXiv 2504.08791](https://arxiv.org/abs/2504.08791), ICLR 2026),
distributed-llama, exo, and llama.cpp's RPC backend. Sanad's contribution is
the *combination* that has never shipped in one living system:

1. **Sharded serving** — each worker hosts a slice of a model's layers, so a
   model too big for any one device is served by a chain of small ones.
   Technically proven by Petals; the Petals public swarm died (~Sept 2024)
   with no incentive layer to sustain it.
2. **Non-tradeable credit fairness** — contribute compute, earn credits, get
   priority when you consume; credits can never be bought or sold. Proven
   live by AI Horde's kudos system (alive as of August 2026) — but Horde
   workers must host full models, so it cannot serve big ones.

This prototype exists so contributors can read, run, and test that
network/fairness logic — the part that *is* Sanad — in a few hundred lines,
before any heavy runtime is attached.

## Run it

Requires Python 3.12+. From this directory:

```
python -m sanad.demo
python -m unittest discover -s tests -v
```

## What the demo shows

- A mock 80-layer `big-70b` (40 GiB at ~4-bit) that **no single worker can
  hold** — the largest registered worker has 24 GiB.
- Registration validation: a worker claiming more layers than its memory can
  hold (`greedy-g`) is **rejected** up front.
- Pipeline assembly: a dynamic program picks the **lowest-latency contiguous
  cover** of all 80 layers from the heterogeneous fleet (8–24 GiB workers),
  Petals-style (a worker entered mid-range serves the suffix of its slice).
- Three clients — one anonymous with zero credits, two contributors with
  earned credits — submit jobs in the *opposite* order of their balances.
  The queue reorders by credit priority: contributors are served first, and
  the anonymous client is still served last. **Spending buys priority, not
  access.**
- Per-worker earnings: each generated token mints exactly 1.0 credit, split
  across the pipeline proportional to layers served; the requester burns 1
  credit per token (clamped at zero for anonymous users).

## What is real vs. mocked

| Simulated faithfully | Mocked away |
|---|---|
| Memory-fit validation of a worker's claimed layer range | Transformer math — a dataclass stands in for the hidden state |
| Pipeline assembly: shortest-latency DP over contiguous layer ranges | Networking — `asyncio.sleep` stands in for WAN hops |
| Credit mint/burn, non-transferability, spend-as-priority queue | Verification of worker outputs (trusted entirely here) |
| No-starvation for zero-credit users | Privacy of activations in transit |

The demo's ~3.6 tok/s pipeline figure is deliberately sober, not a limitation
of the mock: measured WAN split inference reaches only ~8.7–9.3 tok/s for a
7B model at ~80 ms RTT even with lookahead decoding
([arXiv 2602.16760](https://arxiv.org/abs/2602.16760)), and Petals ran at
~1 step/s on BLOOM-176B. WAN latency is the hard cap on single-stream speed.

## Honest limitations (read before extending)

- **No real inference.** Backends are Phase 1 work: adapters for
  llama.cpp RPC, Parallax, and BloomBee, behind the coordinator interface
  sketched here. Nothing in this directory runs a model.
- **Verification is unsolved here and only partially solved anywhere.** This
  simulation trusts workers completely. The state of the art is
  detect-and-eject auditing (TOPLOC,
  [arXiv 2501.16007](https://arxiv.org/abs/2501.16007)), not cryptographic
  proof of correct computation.
- **Activation privacy through untrusted peers is an open problem.** Prompts
  can be reconstructed from intermediate activations with >90% accuracy
  ([arXiv 2503.09291](https://arxiv.org/abs/2503.09291)). Treat this as a
  research agenda, not a solved feature of any sharded swarm, including a
  future Sanad.
- **The ledger is in-memory and unauthenticated.** Persistence, identity,
  Sybil resistance, and abuse controls are design work this prototype does
  not attempt.

## Layout

```
prototype/
  sanad/
    models.py       # ModelSpec, WorkerInfo, Job, CreditEntry
    ledger.py       # CreditLedger — non-transferable by design (see docstring)
    coordinator.py  # registration, pipeline DP, credit-priority queue, dispatch
    worker.py       # async mock worker: latency sleep, forward, earn credits
    demo.py         # python -m sanad.demo
  tests/
    test_sanad.py   # python -m unittest discover -s tests -v
```

License: Apache-2.0.
