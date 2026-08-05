# Sanad: Concept

> **Sanad** (Arabic: سند) means *support* or *backing*. In classical scholarship, the *sanad* is the chain of transmission that authenticates knowledge — each link vouched for, link by link. Sanad the project is a chain of ordinary devices, each hosting a slice of a model too big for any one of them, passing verified work down the line.
>
> **Run AI models too big for any one device — together.**

All factual claims in this document are dated **August 2026** and sourced in [PRIOR_ART.md](PRIOR_ART.md).

---

## The Problem

Open-weight models are free to download and legally free to run. For most of the world, that freedom is theoretical.

**1. Open weights are free, but weak devices cannot run them.** A 70B-parameter model quantized to 4 bits needs roughly 40 GB of memory; frontier open MoE models (DeepSeek 671B, Kimi K2 1T) need hundreds of gigabytes. Typical consumer devices have 8–24 GB. The gap between "the weights are on Hugging Face" and "I can actually run them" is the size of a datacenter GPU budget. Meanwhile, the sum of memory across any group of friends, a university lab, or a hobbyist community easily exceeds what any single member owns.

**2. Free centralized access is a favor, not a right.** In 2026, free access to big open models is genuinely abundant — OpenRouter lists 25+ free frontier-open models, Cerebras gives away 1M tokens/day at ~2,100 tok/s, Groq and SambaNova run no-credit-card free tiers. But every one of these is a revocable marketing expense:

- **It rotates without notice.** OpenRouter's free DeepSeek and Mistral variants were pulled by July 2026; the free lineup changes unpredictably.
- **It is geo-blocked.** Users in sanctioned or firewalled countries (Iran, Cuba, Syria, North Korea; US models in China) are cut off from the major providers entirely.
- **It is payment-rail-gated above the free tier.** Anyone without an international credit card — a large fraction of the Global South — cannot buy their way past the daily caps, no matter how little the tokens cost.

A person whose only access to large open models is someone else's free tier does not have access. They have a subscription to someone else's continued generosity.

**3. The one durable alternative — community infrastructure — does not currently exist for large models.** That is the gap Sanad addresses, and it is a confirmed gap, not an assumed one.

---

## Prior Art and the Confirmed Gap

Sanad combines two ideas that have each been proven separately, in systems that ran (or still run) in public — and that have never been combined.

### Petals proved sharded volunteer inference works — and died for lack of an incentive layer

[Petals](https://github.com/bigscience-workshop/petals) (BigScience, 2022; [arXiv 2312.08361](https://arxiv.org/abs/2312.08361), built on [Hivemind](https://github.com/learning-at-home/hivemind)) was a BitTorrent-style public swarm: each volunteer hosted a slice of a big model's transformer layers, and clients pipelined activations through a chain of volunteers over the internet. It worked: BLOOM-176B at ~1 step/s, Llama 2 70B at up to ~6 tok/s, Falcon-180B at ~4 tok/s — up to 10× faster than local disk offloading on a weak machine.

Then it died. Last substantive commit August 25, 2024; last repository push September 7, 2024; the swarm health monitor (health.petals.dev) refuses connections and chat.petals.dev shows a permanent "out of capacity" error (both checked August 5, 2026). A July 2026 Hacker News post-mortem attributes the collapse to: no incentive mechanism (volunteer GPU supply decayed once novelty faded), 1–4 tok/s speeds against fast free centralized tiers, small local models improving, and unsolved privacy/Sybil concerns. The technology was sound. The *social system* had no reason to keep existing.

### AI Horde proved credit-based fairness works — but its workers must host full models

[AI Horde](https://aihorde.net) (non-profit Haidra) has run a volunteer inference network continuously for years and is alive today: commits as recent as July 29, 2026, and on August 5, 2026 its live API showed 32 text workers serving 25 models at ~30K tokens/min. Its **kudos** system is the canonical fair-share design: contribute compute, earn kudos; spend kudos for queue priority when you consume; anonymous users are served free at lowest priority; and kudos can **never** be bought or sold — trading is banned by the Terms of Service. No token, no speculation, no securities question. It has quietly outlived every crypto-incentivized compute project's hype cycle.

But AI Horde's architecture is a job queue, not a model-parallel system: each worker hosts a *complete* model via KoboldCpp or Aphrodite. So the network's model ceiling is whatever individual volunteers' machines can hold — the August 2026 snapshot was dominated by 0.6B–14B models, with solid pools at ~31B and exactly one 128B worker with long queues. Weak devices can *earn* on the Horde (even CPU-only jobs), but they cannot *pool* to host a big model.

### The gap, verified

As of August 5, 2026, checked live: **no network anywhere combines layer-sharded swarm inference with a non-tradeable credit fairness system.** The sharding side has active successors — [BloomBee](https://github.com/ai-decentralized/BloomBee) (UC Merced, research framework, no public swarm, no incentives) and [Parallax](https://github.com/GradientHQ/parallax) (Gradient, Nov 2025, brings vLLM-class serving to P2P pipelines but has no volunteer network or credit layer) — and the fairness side has AI Horde. Nobody has put the two halves in one living system. Each half's missing piece is exactly what the other half proved.

---

## The Sanad Thesis

**Sanad = a Petals-style sharded swarm + an AI Horde-style non-tradeable credit economy, built and positioned as public infrastructure.**

The framing matters as much as the architecture. Sanad is not trying to win a market; it is trying to exist, the way BitTorrent and Wikipedia exist:

- **BitTorrent** is slower than a CDN and has never mattered less for it. Its value is that no one can turn it off, and that its capacity is the sum of its users'.
- **Wikipedia** is not the best-resourced encyclopedia; it is the one that belongs to everyone and therefore is still there.

A community-owned inference network is worth building even in a world of fast free tiers, *because* those tiers are revocable and unevenly distributed. Sanad's product is not tokens per second. It is the property that access to large open models cannot be rescinded by a pricing-page edit, a sanctions list, or a payment processor.

**Why now, and not in 2023:**

1. **Petals' failure is now diagnosable.** In 2022 nobody knew whether a sharded swarm would fail on technology or on incentives. We now know: the technology worked; the incentive vacuum killed it. That is a fixable defect, and AI Horde has been running the fix in production for years.
2. **The serving stack matured.** Parallax (Nov 2025) demonstrated continuous batching and paged KV-cache — the techniques behind vLLM — running over P2P pipeline parallelism, with 5.3× lower inter-token latency than Petals on comparable hardware. The 2022 swarm was built on 2022 serving tech; a 2026 swarm doesn't have to be.
3. **Verification is partially solved and field-tested.** TOPLOC ([arXiv 2501.16007](https://arxiv.org/abs/2501.16007)) and its pipeline-parallel v2 verified inference across 1,250+ permissionless community GPUs in Prime Intellect's SYNTHETIC-2 run. It is detect-and-eject auditing, not cryptographic proof — but detect-and-eject is exactly what a credit economy needs: cheat, get caught, lose your standing.
4. **MoE economics favor pooled memory.** Modern frontier open models are mixtures-of-experts: total parameters are huge (671B, 1T) but per-token compute scales with *active* parameters (~32–37B). Memory-rich, compute-poor pools — which is what a volunteer swarm is — are the natural host for that shape.
5. **The revocability of free tiers stopped being hypothetical.** The July 2026 OpenRouter free-model pull was small, but it made the argument for resilient community infrastructure concrete.
6. **Public-AI institutions have legitimized the framing.** The [Public AI Inference Utility](https://publicai.co) (launched Sept 2025) explicitly frames AI as public infrastructure like water or electricity — but it runs on donated institutional clusters. Sanad is the complementary answer for the compute that institutions don't own: the world's idle consumer hardware.

---

## What Sanad Is Not

Honesty is a load-bearing design principle here, so this section is not boilerplate.

**Sanad is not a cheaper API.** That positioning killed Petals, and the economics have only gotten worse: a single free Cerebras account today delivers more daily 70B-class tokens, at roughly 500× the speed, than the entire Petals swarm ever did. WAN physics caps a sharded swarm's single-stream speed: at ~80 ms RTT between stages, a 7B model decodes at roughly 9 tok/s even with lookahead decoding ([arXiv 2602.16760](https://arxiv.org/abs/2602.16760)). Anyone who wants the fastest or cheapest tokens should use a centralized provider, and we will say so in our own documentation. Sanad competes on a different axis: durability, ownership, and unconditional access.

**Sanad is not a crypto project.** Credits are non-tradeable by design, following AI Horde's kudos: they cannot be bought, sold, or exchanged for anything except priority service. This is first a fairness decision — the moment credits are tradeable, they become a speculative asset, farming becomes an industry, and the network's purpose bends toward extraction (the 2025–26 record of AI-compute tokens is a catalogue of this failure mode). It is secondarily a legal simplification: a point that can never be sold is very hard to construe as a security, and Sanad simply never enters that arena. No token. No presale. Ever.

**Sanad does not claim to beat datacenter speed — or match it.** See the numbers above. Interactive use of mid-size models and batch/asynchronous use of large models are the honest near-term workloads. We publish our latency math instead of hiding it.

**Sanad is not, today, a privacy solution.** In a pipeline of untrusted peers, intermediate activations transit strangers' machines, and prompt reconstruction from activations has been demonstrated at >90% accuracy ([arXiv 2503.09291](https://arxiv.org/abs/2503.09291), CCS 2025). Until that open problem has real mitigations, Sanad's documentation will state plainly: do not send prompts through the public swarm that you would not send through a stranger's computer — because that is literally what happens. Privacy is a first-class research agenda (below), not a marketing claim.

---

## Design Principles

1. **Non-tradeable credits, forever.** Contribute compute, earn credits; spend credits for priority; anonymous users are served within bounded time (a reserved share of scheduling is strictly first-come-first-served) so the floor of access is zero-cost. Credits can never be bought, sold, or transferred for value, and this is a constitutional commitment, not a launch-phase policy. AI Horde has proven this design sustains a volunteer network for years without speculation. The one thing Petals lacked is the one thing this provides: a durable reason for supply to exist.

2. **Honesty about limits.** Every performance claim dated and sourced; every unsolved problem labeled unsolved. WAN latency caps single-stream speed. Activation privacy through untrusted peers is unsolved. Verification is detect-and-eject (TOPLOC-style), not cryptographic proof. A project asking for the public's hardware owes the public the truth about what that hardware buys.

3. **Standing on prior art, by name.** Sanad invents almost nothing. Sharded swarm inference: Petals, Hivemind, BloomBee, Parallax. Heterogeneous scheduling on weak devices: prima.cpp, distributed-llama, exo. Credit fairness: AI Horde's kudos. Verification: TOPLOC, VeriLLM. Public-infrastructure framing: the Public AI Inference Utility. Our contribution is the synthesis and the institution around it, and we cite our ancestors generously — see [PRIOR_ART.md](PRIOR_ART.md).

4. **Privacy as a first-class research agenda.** The activation-reconstruction attack ([arXiv 2503.09291](https://arxiv.org/abs/2503.09291)) defines our hardest open problem. Candidate directions — deeper split points (token recovery falls from ~59% at a 2-layer split to ~35% at 8 layers), activation noising, trusted-hardware islands within the swarm, and routing sensitive traffic only through vouched-for nodes — are research tracks, and none is shipped anywhere today. Until one is, the threat model stays on the front page.

---

*Sanad is open source under Apache-2.0. This document states the concept; [PRIOR_ART.md](PRIOR_ART.md) documents the landscape it grows from.*
