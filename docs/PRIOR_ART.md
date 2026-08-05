# Prior Art and Landscape

Sanad is a synthesis, not an invention. Nearly every mechanism it uses was built, proven, or disproven by someone else first, and this document is the map of that debt. Statuses were verified in early **August 2026** (live API checks, GitHub activity, primary sources); dates are given throughout. Where a claim rests on secondary sources or inference, it is flagged.

Sections:

1. [Sharded-swarm inference](#1-sharded-swarm-inference)
2. [Credit-based volunteer networks](#2-credit-based-volunteer-networks)
3. [Verification and privacy research](#3-verification-and-privacy-research)
4. [Public-AI institutions](#4-public-ai-institutions)
5. [Economic headwinds](#5-economic-headwinds)

---

## 1. Sharded-swarm inference

The technical half of Sanad: splitting a model's layers across machines so no single machine needs to hold it.

| Project | What it is | Status (Aug 2026) | Lesson for Sanad |
|---|---|---|---|
| **[Petals](https://github.com/bigscience-workshop/petals)** ([arXiv 2312.08361](https://arxiv.org/abs/2312.08361)) | The 2022 pioneer: BitTorrent-style public swarm; volunteers host consecutive transformer blocks, clients pipeline activations through them over the internet (Hivemind DHT). BLOOM-176B at ~1 step/s; Llama 2 70B up to ~6 tok/s; Falcon-180B ~4 tok/s — up to 10× faster than local offloading. | **Effectively dead.** Last substantive commit 2024-08-25, last push 2024-09-07; health.petals.dev refuses connections and chat.petals.dev shows a permanent "out of capacity" error (checked 2026-08-05). No official shutdown notice — death inferred from ~2 years of silence plus unreachable infrastructure. A July 2026 HN post-mortem cites: no incentive layer, 1–4 tok/s vs fast free tiers, better small local models, privacy/Sybil worries. | The founding proof and the founding warning. Sharded volunteer inference *works*; a swarm with no reason to keep contributing *dies*. Sanad exists to fix exactly this defect. |
| **[Hivemind](https://github.com/learning-at-home/hivemind)** | The PyTorch P2P substrate under Petals (and sahajBERT, and Parallax's DHT transport): DHT coordination without a master node, fault-tolerant collectives. | Alive as research infrastructure: last push 2026-01-11, ~2.5k stars. No public volunteer runs of its own. | Battle-tested P2P plumbing exists and is maintained. Sanad should reuse, not rewrite. |
| **[BloomBee](https://github.com/ai-decentralized/BloomBee)** | Academic Petals successor (UC Merced PASA Lab + Yotta Labs + William & Mary): extends Petals/Hivemind/FlexGen with offloading and communication optimizations; targets up to Llama-3.1-405B scale. On PyPI as `bloombee`. | **Most alive Petals descendant**: commits 2026-06-17 (GQA support, Qwen3/Gemma4 attention fixes). But a research framework: no public swarm to join, no incentive layer. | The sharding codebase kept evolving after Petals stopped — modern attention variants and offloading are handled. What BloomBee deliberately doesn't build (a live network with incentives) is Sanad's job. |
| **[Parallax](https://github.com/GradientHQ/parallax)** (Gradient, [whitepaper](https://gradient.network/parallax.pdf)) | P2P pipeline-parallel serving with vLLM-class techniques on decentralized nodes: continuous batching, paged KV-cache, SGLang/CUDA on NVIDIA + MLX on Apple Silicon, DHT/Hivemind transport, heterogeneity-aware scheduler. Works on LAN or over the internet. | Launched 2025-11-06; 40+ models across Windows/macOS/Linux. Benchmarks: Qwen2.5-72B Int4 on 2× RTX 5090 at 40.7 ms inter-token latency (5.3× better than Petals' 216.5 ms), 22 tok/s single-stream / 131 tok/s batched; Qwen3-235B-A22B on 6× RTX 5090 ~16 tok/s; mixed RTX 5090 + 3× Mac M4 Pro at 9.8 tok/s. | The strongest evidence that a 2026 swarm need not run on 2022 serving tech. Petals' performance gaps (no continuous batching, no paged KV, client-side embedding work) are closed problems. Parallax has no volunteer network or credit economy — it is an engine, and a candidate foundation for Sanad's. |
| **[prima.cpp](https://github.com/OpenCPIL/prima.cpp)** ([arXiv 2504.08791](https://arxiv.org/abs/2504.08791), ICLR 2026) | Distributed llama.cpp fork for genuinely weak home clusters: pipelined-ring parallelism overlapping disk I/O with compute, and the Halda scheduler that assigns layers by each device's RAM/VRAM/speed. Phones can join via Termux. | Active; paper accepted ICLR 2026. Llama-70B at ~1.5 tok/s across 4 mixed home devices (Mac M1, i9 laptops, a phone) with <6% memory pressure; 32B + speculative decoding at 26 tok/s. Limits: no Windows, CUDA-only GPU path, no MoE support yet. | Heterogeneity-aware layer assignment is essential and solved-in-principle: real volunteer fleets are wildly mismatched, and naive equal splits waste them. The scheduler literature is ready to borrow. |
| **[distributed-llama](https://github.com/b4rtaz/distributed-llama)** | C++ *tensor*-parallel inference over Ethernet; RAM divides across nodes; requires 2^n nodes and node count ≤ KV-head count. | Active through 2025 (Qwen3 MoE Sept 2025). Best weak-device datapoint anywhere: Qwen3-30B-A3B at 13.04 tok/s on 4× Raspberry Pi 5 (8GB). Largest supported: Llama 3.1 405B Q40 (238 GB). | Tensor parallelism gives near-linear speedups but needs many syncs per layer — LAN-only in practice. For a WAN swarm, pipeline (layer) sharding is the right topology; TP is a tool for co-located sub-clusters within it. |
| **[llama.cpp RPC](https://github.com/ggml-org/llama.cpp/blob/master/tools/rpc/README.md)** | The lowest-friction pooling of mismatched boxes: an rpc-server per worker, master ships ggml ops over TCP, layers split by memory. | Officially still proof-of-concept: "fragile and insecure. Never run the rpc-server on an open network" — no encryption, no auth; identical builds required on all nodes. Hobbyist rigs ran DeepSeek 671B Q4 at ~3.8 tok/s. | What the most popular local-inference stack ships for distribution is explicitly unfit for open networks. A public swarm must treat transport security and version negotiation as day-one requirements, not add-ons. |
| **[Cake](https://github.com/evilsocket/cake)** | Rust/Candle framework sharding Llama 3 / Stable Diffusion across iOS, Android, macOS, Linux, Windows workers via mDNS — the only framework that seriously targeted phones as workers. | Effectively dormant: zero releases ever published, ~3.1k stars (maintenance status inferred from empty releases page — low confidence). | Phones-as-workers is possible but nobody has made it stick. Ambition without a sustaining community is the recurring failure shape; Sanad should treat mobile workers as a later phase, not a launch promise. |
| **[exo](https://github.com/exo-explore/exo)** (EXO Labs) | Clusters devices *you own* (Apple-Silicon-focused; Linux CPU-only) with automatic discovery and topology-aware partitioning. EXO 1.0 (Dec 2025) added RDMA over Thunderbolt 5 (per-hop latency ~300 µs → <50 µs). | Very much alive: ~46.6k stars, last push 2026-06-23. Holds the consumer-hardware record: Kimi K2 Thinking (1T params, 4-bit) at 28.3 tok/s and DeepSeek V3.1 671B (8-bit) at 32.5 tok/s on 4× M3 Ultra Mac Studios (1.5 TB pooled memory, ~$40k). Also demonstrated prefill/decode disaggregation across mismatched hardware for 2.8× throughput. | Two lessons. (1) The ceiling: pooled consumer hardware genuinely serves frontier-scale MoE models — on a trusted LAN. (2) The strategic retreat: exo deliberately abandoned the public-swarm idea for owner-trusted clusters, sidestepping incentives, trust, and WAN latency. Sanad is choosing to walk back into exactly those three problems — knowingly, with the mitigations named. |
| **[SharedLLM](https://sharedllm.org)** | Community-owned distributed network on llama.cpp's RPC backend; AGPL-3.0, non-profit governance. | v0.1.0 alpha (per its own comparison article, 2026-04-11): only a toy 260K-parameter test model verified end-to-end; no credit system. Watch-list only. | Others see the same gap. Good intentions plus llama.cpp RPC does not equal a network; the engine and the incentive layer both have to be real. |

**The physics that constrains all of it.** Pipeline sharding moves only one hidden-state vector per token per stage boundary (~10–30 KB), so LAN hops (0.1–1 ms) are nearly free — but WAN RTT is the hard cap on single-stream decode. A Feb 2026 measurement study ([arXiv 2602.16760](https://arxiv.org/abs/2602.16760)) got 8.7–9.3 tok/s for a 7B model over ~80 ms RTT links even with lookahead decoding, projecting 15–19 tok/s at 20 ms RTT. Batched throughput scales much better than single-stream latency — which is why Sanad's honest workloads are interactive mid-size models, and batch/asynchronous large-model jobs. MoE helps the memory economics (decode cost scales with *active* params — 32B of Kimi K2's 1T), but shipping frameworks still shard MoE by layer; expert-level sharding across consumer links (MoEShard, EdgeMoE, SiftMoE) remains research because per-token expert routing is all-to-all and latency-hostile.

**Rule-of-thumb memory math** (usable pool ≈ 0.7 × summed RAM; GGUF Q4 ≈ 0.55–0.6 GB per B params; each node must hold ≥ 1 full layer): eight 8 GB devices → a 70B Q4 dense or 30B-A3B MoE comfortably; eight 16 GB → GPT-OSS-120B class; eight 24 GB → 671B only at extreme ~1.6-bit quantization with thin KV headroom. A 671B Q4 pool needs ~0.5 TB — Mac-Studio-class nodes.

---

## 2. Credit-based volunteer networks

The social half of Sanad: why anyone contributes, and why the credits must never be money.

### AI Horde — the living proof

**[AI Horde](https://aihorde.net)** (non-profit [Haidra](https://github.com/Haidra-Org/AI-Horde)) is, as of August 2026, the only live, non-crypto, credit-based volunteer inference network in existence — and it has been running for years. Verified 2026-08-05 via its live API: 32 text workers (49 threads) serving 25 text models at ~30K tokens/min, plus image and CPU-only "Alchemist" workers; main-repo commits as recent as 2026-07-29.

Its **[kudos](https://github.com/Haidra-Org/haidra-assets/blob/main/docs/kudos.md)** mechanics, which Sanad adopts nearly wholesale:

- Earned by processing requests; spent as **queue priority** when consuming.
- **Never purchasable or sellable** — trading is banned by the Terms of Service. No token, no speculation, no securities exposure.
- Never expire; giftable.
- Anonymous users are served free at lowest priority — the floor of access is zero-cost.
- Even GPU-less users can earn (CPU-only post-processing jobs).

Its **architectural ceiling**, which Sanad exists to remove: every worker hosts a *complete* model (via KoboldCpp or Aphrodite Engine — the Horde distributes jobs, not shards). The August 2026 model mix was therefore mostly 0.6B–14B, with real pools at ~31B (7–8 workers each) and a single 128B worker with ~18-minute ETAs. Weak devices can earn on the Horde but cannot pool to host anything big.

**Lesson:** the kudos design is the proven answer to the question that killed Petals, and full-model workers are the proven limitation that Petals answered. The two systems are jigsaw pieces. (Sanad also notes what the Horde's niche actually is — image generation and hobbyist/roleplay text — as a sober signal about where organic volunteer demand lives.)

### Kalavai — the near miss

**[Kalavai](https://github.com/kalavai-net/kalavai-client)** aggregated spare GPUs into community pools where contributors earned platform credits usable in future pools — including, notably, [a pool dedicated to hosting Petals workers](https://kalavainet.substack.com/p/kalavai-welcomes-bittorrent-style): the closest anyone came to bolting a credit layer onto a sharded swarm. The repo was active through 2025 (Ray, AMD, ARM support), but positioning pivoted to an enterprise inference platform; whether any public credit-earning pool still operates in 2026 is **unverified**. **Lesson:** the combination was glimpsed once, as a side project of a company that needed revenue. Public infrastructure can't be a pivot away from someone's business model; it needs an institution whose only job is to exist.

### BOINC-family precedents — volunteer computing at planetary scale

| Project | What it proved | Status (Aug 2026) | Lesson for Sanad |
|---|---|---|---|
| **[BOINC](https://boinc.berkeley.edu)** (2002–) | Millions of home PCs will run science workloads for decades — for *embarrassingly parallel* tasks. Its credit system was pure scoreboard: never spendable on anything. | Alive: client 8.2.11 (June 2026), ~26 active projects. Its one ML project (MLC@Home) shut down Oct 2022. No BOINC project does LLM inference. | Volunteer compute at scale is real, but the bag-of-tasks model doesn't fit tightly coupled LLM work — and credits that buy nothing rely purely on altruism/leaderboards. Sanad's credits buy priority, a concrete personal benefit. |
| **SETI@home** (1999–2020) | A compelling narrative recruits millions of volunteers for 20 years. | Hibernating since 2020-03-31; final analysis papers published 2025. | Narrative is a real incentive — and an exhaustible one. Story alone doesn't sustain supply forever; a reciprocity loop can. |
| **[Folding@home](https://foldingathome.org)** (2000–) | Cause-driven volunteering can transiently rival supercomputers: ~2.4 exaFLOPS at the April 2020 COVID peak (~700k new volunteers vs ~30k baseline), receding after. | Still active: v8.5.5 (Dec 2025). | Participation is event-driven and collapses when the moment passes. Design for the trough, not the spike. |

---

## 3. Verification and privacy research

The two hard open(-ish) problems. Sanad's position: verification is *partially solved — good enough to start, honestly labeled*; activation privacy is *unsolved — stated as a threat model and a research agenda, never papered over*.

### Verification: detect-and-eject is deployed; cryptographic proof is not

- **[TOPLOC](https://arxiv.org/abs/2501.16007)** (Prime Intellect, 2025): commits locality-sensitive hashes of top-k intermediate activations — 258 bytes per 32 tokens (~1000× smaller than raw activations), validation up to ~100× faster than re-inference, empirically zero false positives/negatives across GPU types. **TOPLOC v2** extends it to pipeline-parallel P2P: group-level verification (a correct final output vouches for all stages) plus stage-by-stage blame replay to identify and eject a faulty worker. Battle-tested in [SYNTHETIC-2](https://www.primeintellect.ai/blog/synthetic-2) (mid-2025): verified pipeline-parallel inference across **1,250+ permissionless community GPUs** (RTX 4090 to H200).
- **[VeriLLM](https://arxiv.org/abs/2509.24257)** (2025) and communication-efficient verifiable attention ([arXiv 2606.16352](https://arxiv.org/abs/2606.16352)) are the active follow-on line.
- **What this is and isn't:** optimistic *detect-and-eject auditing* that assumes a trusted verifier able to re-execute — not a cryptographic guarantee. zkML proofs remain impractical at 100B+ scale, and fully trustless anonymous P2P inference is an open problem.

**Lesson:** detect-and-eject is precisely the right shape for a credit economy — cheating is caught statistically and punished economically (credits and standing lost), which is how AI Horde already polices quality, and TOPLOC v2's blame assignment maps one-to-one onto a Sanad pipeline. Sanad states plainly that this is deterrence, not proof.

### Privacy: the open problem, stated without flinching

- **[Prompt Inference Attack on Distributed LLM Inference Frameworks](https://arxiv.org/abs/2503.09291)** (Luo, Yu, Xiao — CCS 2025): reconstructs input prompts from intermediate activations with **>90% accuracy** given auxiliary data, >50% with limited queries. This is an attack on exactly the architecture Sanad proposes.
- The Feb 2026 WAN study ([arXiv 2602.16760](https://arxiv.org/abs/2602.16760)) measured ~59% token recovery at a 2-layer split falling to ~35% at an 8-layer split — split depth helps, and nowhere near enough.
- **No deployed consumer P2P system protects activations from a malicious peer.** llama.cpp RPC ships zero encryption/auth; exo, Parallax, and prima.cpp all assume you own or trust every node. Research directions — deeper splits, activation noising, TEE islands, vouched-node routing — have shipped nowhere.

**Lesson:** Sanad treats [arXiv 2503.09291](https://arxiv.org/abs/2503.09291) as a first-class design constraint: the public swarm is documented as *not private*, sensitive workloads are explicitly out of scope until mitigations exist, and privacy research is on the roadmap as an agenda — not a checkbox.

---

## 4. Public-AI institutions

Sanad's framing — AI access as public infrastructure — is not novel either, and says so.

| Institution | What it is | Status (Aug 2026) | Lesson for Sanad |
|---|---|---|---|
| **[Public AI Inference Utility](https://publicai.co)** | Non-profit "Wikipedia-style" access point serving sovereign/public open models via vLLM on **donated institutional clusters**; explicit "AI as public infrastructure like highways, water, electricity" framing. Metagov-supported; listed as a Hugging Face Inference Provider. | Launched 2025-09-02; serves Apertus 1.5, EuroLLM, ALIA, SEA-LION with partners (CSCS, AWS, Intel, Exoscale) across Switzerland, Germany, Australia, Singapore. | The closest existing realization of public-AI infrastructure — and the proof the framing has institutional legs. Its compute comes from donors and datacenters; Sanad is the complementary bottom-up layer built from the hardware the public *already owns*. Ally, not competitor. |
| **[Apertus](https://www.swiss-ai.org)** (Swiss AI Initiative: EPFL / ETH Zürich / CSCS) | Fully open (weights + data + recipe) multilingual 8B and 70B models with a public-service mandate, trained on the Alps supercomputer. | Apertus 1.5 released 2026-07-24 (image understanding, longer context); CSCS launched an inference API for Swiss researchers in 2026. | Public-mandate open models exist and keep improving — ideal candidates for a community swarm to serve. Sanad needs to host models like this, not train its own. |
| **OpenEuroLLM + EuroHPC AI Factories** | EU program building open multilingual models on public supercomputers; AI Factories provide free compute to European researchers, startups, SMEs. | >10M GPU hours granted (LUMI, Leonardo, Jupiter, MareNostrum5); 19 AI Factories + 13 antennas operational; EuroHPC Federation Platform launched 2026-04-15. | Public compute at serious scale exists — gated by geography and institutional affiliation. Public infrastructure that requires EU credentials is public for Europe. A volunteer swarm has no such gate; that non-gate is Sanad's distinct contribution. |

---

## 5. Economic headwinds

Sanad's positioning ("not a cheaper API") is forced by the market, and it is honest to show the numbers that force it.

### Free and near-free centralized access (mid-2026 snapshot)

| Provider | Free tier (2026) | Notes |
|---|---|---|
| **OpenRouter** | 25–28+ ":free" frontier-open models (DeepSeek R1, Llama 3.3 70B, Qwen3 Coder 480B); 20 req/min; 50 req/day (1,000/day after a one-time $10 credit) | **The lineup rotates without notice — free DeepSeek/Mistral variants were pulled by July 2026.** Figures from secondary aggregators; treated as indicative. |
| **Cerebras** | 1,000,000 tokens/day free, ~2,100 tok/s on Llama 3.3 70B (8K context cap) | A single free account delivers more daily 70B-class tokens at ~500× the speed than the entire Petals swarm ever did. |
| **Groq** | ~14,400 req/day, no credit card | Paid 70B-class at $0.59–0.79/M tokens at 300–1,000 tok/s. |
| **SambaNova** | Email-signup free access | Custom silicon, similar economics. |
| **DeepSeek API** | Not free, but nearly: V4-Flash $0.14/M input ($0.0028 cache-hit), $0.28/M output (Aug 2026) | Sets the economic floor; also serves regions US providers geo-block. |

Add the state layer: South Korea's free "AI for Everyone" chatbot for all citizens (bidding July 2026), Malaysia's free access for 100k youths, the ITU AI for Good Lab pooling compute for 118 developing economies (announced 2026-07-28), IndiaAI's ~38k subsidized GPUs. Governments are absorbing much of the population a community network would serve — unevenly, and usually tied to citizenship or approved institutions.

**Every row above is a revocable favor** — rotating free lineups, rate caps, geo-blocks, payment-rail gates, and political conditions. That is simultaneously why Sanad cannot compete on price or speed, and why it should exist at all.

### The Densing Law: shrinking need at the middle

Tsinghua's "Densing Law" ([arXiv 2412.04315](https://arxiv.org/abs/2412.04315), *Nature Machine Intelligence*): LLM capability density doubles roughly every 3.3 months. Empirically by 2026: Llama 3.3 70B matches Llama 3.1 405B; Qwen3-32B matches Qwen2.5-72B; gpt-oss-20b (~o3-mini level) runs on a 16 GB laptop. Much of what needed a 405B swarm in 2024 fits a single consumer GPU today — this is a large part of why Petals lost its audience, and any honest pitch for Sanad must account for it.

**What survives the trend:** the frontier of open models keeps moving up even as capability trickles down — the best open models in 2026 are 100B–1T-parameter MoEs that no consumer device will hold for the foreseeable future. The swarm's honest value is at that moving frontier, plus the resilience argument that no local model and no free tier provides: infrastructure that is *there*, for everyone, regardless of what any provider decides next quarter.

### What the headwinds dictate

1. **Never sell speed or price.** Cerebras wins that contest by orders of magnitude. (This is the mistake that killed Petals' positioning.)
2. **Sell existence:** unconditional, non-revocable, community-owned access to frontier-scale open models — BitTorrent's value proposition, not a CDN's.
3. **Target the workloads WAN physics allows:** interactive mid-size models; batch and asynchronous frontier-MoE jobs.
4. **Measure success in resilience** (nodes, regions, models kept servable, uptime through provider policy changes), not in tokens per second.

---

## Summary of debts

| Sanad takes… | …from |
|---|---|
| Layer-sharded swarm architecture | Petals ([arXiv 2312.08361](https://arxiv.org/abs/2312.08361)), Hivemind |
| Modern P2P serving (continuous batching, paged KV) | Parallax (GradientHQ, Nov 2025) |
| Post-Petals sharding engineering | BloomBee (UC Merced) |
| Heterogeneity-aware scheduling for weak devices | prima.cpp ([arXiv 2504.08791](https://arxiv.org/abs/2504.08791)), distributed-llama, exo |
| Non-tradeable credit fairness | AI Horde / Haidra kudos |
| Verification (detect-and-eject, pipeline blame) | TOPLOC / v2 ([arXiv 2501.16007](https://arxiv.org/abs/2501.16007)), VeriLLM |
| The named open problem in privacy | [arXiv 2503.09291](https://arxiv.org/abs/2503.09291) (CCS 2025) |
| WAN latency ground truth | [arXiv 2602.16760](https://arxiv.org/abs/2602.16760) |
| Public-infrastructure framing | Public AI Inference Utility (publicai.co) |
| Cautionary tales | Petals (incentives), Cake (sustainability), Kalavai (institutions vs. pivots), crypto-compute tokens (speculation) |

*All statuses as of August 2026. Corrections welcome — file an issue.*
