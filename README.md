# Sanad — سند

> **Run AI models too big for any one device — together.**

**Sanad** (Arabic: *سند*, "support" — and the classical term for the **chain of transmission** that carries knowledge from person to person, each link vouching for the next) is a community inference network for large open-weight language models. Nodes each hold a *slice* of a model's layers; chained together, they serve models none of them could run alone. Contributing compute earns **non-tradeable credits** that give you priority when you use the network.

**Status: the network is alive (v0.2, 2026-08-05).** Two proven milestones in one day, both with full captured transcripts in [docs/PROOF.md](docs/PROOF.md):

- **First Light** — a real model's layers physically split across two node processes over TCP, real text generated through the chain, credits earned by serving and spent as priority.
- **The Living Network** — the network now *breathes*: a **capacity ladder** automatically serves the largest model the community's pooled memory can hold (a second node joining upgraded the model live; a node leaving downgraded it — service never stopped); the **polite node** runs at low OS priority, senses when its owner needs the machine (a CPU-hungry "game" started → it drained out by itself, returning all memory → rejoined by itself when the machine calmed); and credits are **weighted by layer share**, so memory lent equals share earned — with balances fully kept when a node withdraws. Your device, your priority, always.

Still day one: single-machine, trusted nodes, small models — the honest scope is in the proof doc. Founding contributors wanted: read the [Concept](docs/CONCEPT.md) and open an issue.

اقرأ الملخص بالعربية: [README.ar.md](README.ar.md)

---

## Why

The best open-weight models — DeepSeek, Llama, Qwen, Kimi — are published free. But they are 400GB–1TB of weights: no personal device can run them. So in practice, "open" models are only open to whoever owns a datacenter.

Today you can borrow access through free API tiers. That access is a **revocable favor**: free model lineups rotate and vanish without notice, whole countries are geo-blocked, and anyone without an international credit card is locked out of paid tiers. Open knowledge that depends on a vendor's goodwill is not open.

Wikipedia wasn't built because commercial encyclopedias were bad. It was built so that knowledge wouldn't belong to anyone who could take it away. Sanad applies the same reasoning to running open models: **public infrastructure, owned by the people who run it.**

## The gap Sanad fills

Two ideas have each been proven — but never combined in one living system (verified against the landscape as of **August 2026**; see [Prior Art](docs/PRIOR_ART.md)):

| Idea | Proven by | Fate |
|---|---|---|
| **Shard a big model across weak devices** (each node hosts a few layers, requests flow through the chain) | [Petals](https://github.com/bigscience-workshop/petals) (2022) — served BLOOM-176B and Llama-70B on volunteer GPUs | Swarm effectively **dead** since ~2024 — it had **no incentive layer**, so volunteers drifted away |
| **Non-tradeable credit fairness** (contribute compute → earn credits → spend as queue priority; credits can never be bought or sold) | [AI Horde](https://aihorde.net)'s *kudos* system — **alive and healthy** in 2026 | Works — but every Horde worker must host a **full** model, so large models are nearly absent |

**Sanad = Petals' sharding + AI Horde's fairness.** Sharding without incentives died; incentives without sharding can't serve big models. Nobody runs both. That is the whole project.

## What Sanad is not

Honesty is a design principle here (over-promising is how this field loses trust):

- **Not a cheaper/faster API.** Centralized free tiers will beat a swarm on speed every time. A swarm's value is *resilience and ownership*, not benchmarks. Positioning Petals as "free inference" is part of what killed it.
- **Not a crypto project.** Credits are non-tradeable by the Terms of Service, forever — no token, no speculation, nobody's savings at risk. This is a founding commitment; see [GOVERNANCE.md](GOVERNANCE.md).
- **Not claiming solved problems are solved.** Single-stream speed over the internet is capped by round-trip latency (~9 tok/s for a 7B at 80 ms RTT in published measurements). Privacy of activations passing through untrusted peers is an **unsolved research problem** (prompts can be reconstructed from intermediate activations — arXiv 2503.09291). Verification of honest computation is only partially solved (TOPLOC-style detect-and-eject auditing, not cryptographic proof). Sanad starts with **known, registered operators** and treats these three problems as its open research agenda — see [ARCHITECTURE.md](docs/ARCHITECTURE.md).

## How it works (v0 design)

```
 client ──► coordinator ──► worker A (layers 0–29)
                │               │ activations (~KBs/token)
                │               ▼
                │           worker B (layers 30–59)
                │               │
                │               ▼
                │           worker C (layers 60–79)
                │               │
    credits ◄───┴───────────────┘ tokens stream back
```

- **Workers** register a contiguous layer range they can hold; the **coordinator** assembles the lowest-latency chain that covers the whole model and routes jobs by requester credit priority (anonymous users are always served, at lowest priority — no paywall, ever).
- Serving verified tokens **earns credits**; credits buy **priority**, nothing else.
- Sanad does not reinvent inference: real backends plug in via adapters (llama.cpp RPC, [Parallax](https://github.com/GradientHQ/parallax), [BloomBee](https://github.com/ai-decentralized/BloomBee)). Sanad's contribution is the **network and fairness layer** on top.

## Try it

**The real thing** ([net/](net/)) — actual sharded inference through llama.cpp's RPC backend, with Sanad's coordinator and credit ledger on top (needs the llama.cpp binaries + a small GGUF model, setup in [net/README.md](net/README.md)):

```bash
cd net
python -m unittest discover -s tests -v      # 7 tests, no binaries needed
python proof/run_first_light.py              # full network + proof, ends in "FIRST LIGHT: PASS"
```

**The simulation** ([prototype/](prototype/)) — dependency-free model of the network semantics (sharding, pipeline assembly, credit priority), useful for understanding and testing the fairness logic:

```bash
cd prototype
python -m sanad.demo          # watch a "70B" model run across 6 small mock workers
python -m unittest discover -s tests -v
```

## Documents

- [**Concept**](docs/CONCEPT.md) — the problem, the confirmed gap, the thesis
- [**Prior Art**](docs/PRIOR_ART.md) — the full annotated landscape (Aug 2026), every project this stands on
- [**Architecture**](docs/ARCHITECTURE.md) — v0 design: roles, credit ledger, trust model, wire sketches
- [**Roadmap**](docs/ROADMAP.md) — phases with honest exit criteria and kill-criteria
- [**Contributing**](CONTRIBUTING.md) · [**Governance**](GOVERNANCE.md) · [**Code of Conduct**](CODE_OF_CONDUCT.md)

## Standing on shoulders

Sanad exists because of the people who built and published: **Petals** & **Hivemind** (BigScience/Yandex/HSE — the original swarm), **AI Horde / Haidra** (the kudos economy that proved fair volunteer networks can last), **Parallax** (GradientHQ), **BloomBee** (UC Merced PASA Lab), **prima.cpp**, **distributed-llama**, **exo**, **llama.cpp**, **TOPLOC** (Prime Intellect), and the **Public AI Inference Utility**. If you're from any of these communities: we would rather build this *with* you than beside you — please open an issue.

## License

[Apache-2.0](LICENSE). The knowledge chain stays open.
