# Sanad — سند

> **Run AI models too big for any one device — together.**

**Sanad** (Arabic: *سند*, "support" — and the classical term for the **chain of transmission** that carries knowledge from person to person, each link vouching for the next) is a community inference network for large open-weight language models. Nodes each hold a *slice* of a model's layers; chained together, they serve models none of them could run alone. Contributing compute earns **non-tradeable credits** that give you priority when you use the network.

**Status: use it for anything (v0.7).** Six proven milestones, each with a full captured transcript in [docs/PROOF.md](docs/PROOF.md):

- **First Light** — a real model's layers physically split across two node processes over TCP, real text generated through the chain, credits earned by serving and spent as priority.
- **The Living Network** — the network *breathes*: a **capacity ladder** automatically serves the largest model the community's pooled memory can hold (a second node joining upgraded the model live; a node leaving downgraded it); the **polite node** runs at low OS priority, senses when its owner needs the machine (a CPU-hungry "game" started → it drained out by itself → rejoined by itself when the machine calmed); and credits are **weighted by layer share**. Your device, your priority, always.
- **It Works** — the pipeline is now **resident**, so the model is not reloaded per question: time-to-first-token dropped from **5.17s to 0.07s (75x)** in the captured run. Tokens **stream** as they are generated, there's a **chat page** at the coordinator's address that anyone can use in a browser, and it all runs over a **real LAN address**, not loopback.
- **Usable Together** — joining takes **one command and no address** (`--discover` finds the coordinator by broadcast); the network **holds a conversation** (full history through the model's own chat template); credits are **written to an append-only ledger** that survived the coordinator being killed mid-flight (verified by replaying the file with the coordinator dead); and several people are **served at once** (3x the throughput of one-at-a-time).

- **Two Networks** — each node now runs in its own Linux container on its own network, unable to even resolve the other; only the coordinator bridges them. A 1.5B model shards across the two (layers 0–17 / 18–28) and answers correctly. And the honest number: adding 40 ms of latency per link drops throughput from **31.3 to 4.2 tok/s (7.5x slower)** — splitting a model across a network means every token crosses every hop. Sanad is for models too big for one device, not for speed.

- **For Everything** — the coordinator speaks the **OpenAI-compatible API**, so any agent, editor, or script points at it and works with no changes. Verified by driving Sanad with the official OpenAI client, which has never heard of this project. No subscription, no key, no permission.

Still early: trusted nodes, small models, one physical host (a [free-cloud guide](net/deploy/README.md) covers crossing real ISPs) — the honest scope is in the proof doc. Founding contributors wanted: read the [Concept](docs/CONCEPT.md) and open an issue.

**[sanad site →](https://a-alwabel.github.io/sanad/)** · اقرأ الملخص بالعربية: [README.ar.md](README.ar.md)

---

## Why

The best open-weight models — DeepSeek, Llama, Qwen, Kimi — are published free. But they are 400GB–1TB of weights: no personal device can run them. So in practice, "open" models are only open to whoever owns a datacenter.

Today you can borrow access through free API tiers. That access is a **revocable favor**: free model lineups rotate and vanish without notice, whole countries are geo-blocked, and anyone without an international credit card is locked out of paid tiers. Open knowledge that depends on a vendor's goodwill is not open.

Wikipedia wasn't built because commercial encyclopedias were bad. It was built so that knowledge wouldn't belong to anyone who could take it away. Sanad applies the same reasoning to running open models: **public infrastructure, owned by the people who run it.**

## Two properties that define it

**It grows and shrinks with the people in it.** The network always serves the
largest model the pooled memory can hold. Someone joins with a spare laptop and
the whole network upgrades to a bigger model; they leave and it steps back down
without stopping. Nobody administers this — it is the capacity ladder, and it
is in the code, not the roadmap ([proof](docs/PROOF.md)).

**You can point anything at it.** The coordinator speaks the OpenAI-compatible
API, so your agent, your editor, your script, your app — anything that already
talks to a hosted model — works against your own community's network instead.
Same tools, no subscription, no key, no company deciding whether you may.

```python
from openai import OpenAI
client = OpenAI(base_url="http://your-coordinator:7860/v1", api_key="not-needed")
client.chat.completions.create(model="qwen2.5-1.5b-instruct-q4_k_m",
                               messages=[{"role": "user", "content": "hello"}])
```

That is the whole argument: we built the thing, so we should not have to rent
it back.

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

- **Workers** register a contiguous layer range they can hold; the **coordinator** assembles the lowest-latency chain that covers the whole model and routes jobs by requester credit priority — anonymous users are served within bounded time: every third queue slot is strictly first-come-first-served regardless of credits (anti-starvation), the rest go to contributors first. No paywall, ever.
- Serving verified tokens **earns credits**; credits buy **priority**, nothing else.
- Sanad does not reinvent inference: real backends plug in via adapters (llama.cpp RPC, [Parallax](https://github.com/GradientHQ/parallax), [BloomBee](https://github.com/ai-decentralized/BloomBee)). Sanad's contribution is the **network and fairness layer** on top.

## Try it

**Run it** — two commands, ~1.6 GB of disk, Python 3.12+:

```bash
cd net
python -m sanad_net.setup     # fetches llama.cpp for your machine + two small models
python -m sanad_net.run       # starts a network and opens the chat page
```

`setup` says what it will download before it does, and puts everything in a single `.local/` folder you can delete. Anyone else on your network joins with `python -m sanad_net.run --join` — no address to type. Details and manual control: [net/README.md](net/README.md).

Reproduce any of the recorded proofs:

```bash
python net/proof/run_two_networks.py   # each node on its own network (needs Docker)
python net/proof/run_v04.py            # conversations, durable credits, concurrency
python net/proof/run_it_works.py       # resident pipeline, streaming, chat page
```

**Just want to see the idea in 10 seconds?** No downloads, no setup:

```bash
cd prototype && python -m sanad.demo
```

**The simulation** ([prototype/](prototype/)) — dependency-free model of the network semantics (sharding, pipeline assembly, credit priority), useful for understanding and testing the fairness logic:

```bash
cd prototype
python -m sanad.demo          # watch a "70B" model run across a fleet of 6 small mock workers (5 selected into the pipeline)
python -m unittest discover -s tests -v
```

## Documents

- [**Concept**](docs/CONCEPT.md) — the problem, the confirmed gap, the thesis
- [**Prior Art**](docs/PRIOR_ART.md) — the full annotated landscape (Aug 2026), every project this stands on
- [**Architecture**](docs/ARCHITECTURE.md) — v0 design: roles, credit ledger, trust model, wire sketches
- [**Roadmap**](docs/ROADMAP.md) — phases with honest exit criteria and kill-criteria
- [**Science**](docs/SCIENCE.md) — what six disciplines say about this design, what we changed because of them, and what stays open
- [**Decisions**](docs/DECISIONS.md) — why things are the way they are, including what we got wrong and reversed
- [**RFC 0001: The Human Loop**](docs/rfc/0001-human-loop.md) — v0.3 design under community review: rate answers, earn credits, build the first fully open preference dataset — with the integrity defenses first and training explicitly deferred
- [**Contributing**](CONTRIBUTING.md) · [**Governance**](GOVERNANCE.md) · [**Code of Conduct**](CODE_OF_CONDUCT.md)

## Standing on shoulders

Sanad exists because of the people who built and published: **Petals** & **Hivemind** (BigScience/Yandex/HSE — the original swarm), **AI Horde / Haidra** (the kudos economy that proved fair volunteer networks can last), **Parallax** (GradientHQ), **BloomBee** (UC Merced PASA Lab), **prima.cpp**, **distributed-llama**, **exo**, **llama.cpp**, **TOPLOC** (Prime Intellect), and the **Public AI Inference Utility**. If you're from any of these communities: we would rather build this *with* you than beside you — please open an issue.

## License

[Apache-2.0](LICENSE). The knowledge chain stays open.
