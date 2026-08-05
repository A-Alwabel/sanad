# Sanad Roadmap

> Status: DRAFT / RFC, August 2026. Every phase has explicit exit criteria and explicit
> kill criteria. If a kill criterion fires, we stop, publish why, and revisit the design
> in the open rather than scaling a thing that does not work. Petals proved the
> technology and still died; we treat honest checkpoints as a survival feature.

## Phase 0 — RFC, simulation, community (now)

Design in public before building. Petals-style sharding and AI Horde-style kudos are
both proven separately; the open question is whether the combination holds up under
scrutiny from the people who built the priors.

**Work:**
- Publish ARCHITECTURE.md as an RFC; solicit review explicitly from the Haidra (AI
  Horde), Parallax (GradientHQ), and BloomBee (UC Merced PASA Lab) communities, plus
  Hivemind/prima.cpp/exo authors where they'll engage.
- Discrete-event simulation of the swarm: pipeline assembly, churn, credit flows, Sybil
  scenarios, queue fairness — calibrated with published WAN numbers (arXiv 2602.16760)
  rather than optimistic guesses.
- Backend adapter spike: drive llama.cpp RPC (LAN only, per its own "fragile and
  insecure" warning) and Parallax through a common adapter interface on 2–3 machines.

**Exit criteria:** ≥10 engaged contributors (issues/PRs/design review, not stars);
written design feedback from at least two of the three named upstream communities;
simulation results published, including the failure cases.

**Kill criteria:** if upstream review surfaces a structural flaw in the credit/sharding
combination that we cannot answer on paper, we do not proceed to hardware.

## Phase 1 — Trusted-federation MVP

5–10 **known, named operators** (permissioned, per ARCHITECTURE.md §5) serve **one real
mid-size open model** — a ~30–70B-class dense model (e.g. Qwen3-32B or a Llama-70B-class
model, chosen with operators by VRAM reality) — end-to-end with **real credit
accounting**: every served token earns sanad points, every prioritized request spends
them, ledger exports published.

LAN-first (prove correctness), then WAN (prove the actual product). Both llama.cpp RPC
(wrapped in authenticated tunnels) and Parallax/BloomBee adapters exercised; pick the
engine that survives contact with heterogeneous volunteer hardware.

**Exit criteria:**
- The target model streams end-to-end across ≥3 WAN-separated operators for 30
  consecutive days with ≥95% job completion.
- Credit ledger balances match independently recomputed totals from published receipts.
- At least one operator joins and one leaves without manual coordinator surgery.

**Kill criteria (stated before we start, so we can't move the goalposts):**
- **If single-stream decode cannot beat 5 tok/s on the target model across real WAN
  links after tuning, we stop and revisit the architecture before any scaling.**
  (Published baseline: ~9 tok/s for 7B at 80 ms RTT — a 30–70B model across volunteer
  links has real headroom risk; this number decides whether the product is usable or a
  demo.)
- If aggregate batched throughput cannot reach ~50 tok/s on the pipeline, the "public
  infrastructure" framing fails economically too — same stop-and-revisit.

## Phase 2 — Public testnet

Open worker registration behind **spot-audit gating**: TOPLOC-style activation
commitments (arXiv 2501.16007) verified by trusted auditors, detect-and-eject on
mismatch, earning rate-limits on young identities. Honest framing: this is auditing,
not cryptographic proof; the trust model page says exactly what an attacker can still do.

Published, permanent **uptime/latency/queue dashboards** (as AI Horde does) — the
network's health must be checkable by anyone, especially when it's bad.

**Exit criteria:** ≥25 active workers including ≥10 from outside the founding group;
≥1 detected-and-ejected faulty/cheating worker handled by process, not improvisation;
30-day public dashboard history; anonymous-tier usage demonstrably served (fairness
floor works).

**Kill criteria:** if Sybil/farming abuse consumes >20% of issued credits despite
mitigations, freeze open registration and return to the drawing board on admission
control; if volunteer supply cannot sustain one full pipeline of the flagship model
24/7, the incentive layer is not working — diagnose before expanding model list.

## Phase 3 — Open swarm

Verification-gated open membership (no manual approval; audit performance is the gate).
Coordinator federation begins here — multiple mutually-recognizing coordinator instances
so no single operator (including us) is a point of failure or control.

Frontier-scale **MoE targets**: MoE decode cost scales with active parameters (e.g. 32B
active of Kimi-K2-class 1T totals), which fits memory-rich/FLOP-poor volunteer pools —
but shipping frameworks still shard MoE by layer, and expert-level sharding across
consumer devices remains research (MoEShard, EdgeMoE lineage). We target
layer-sharded MoE first and treat expert sharding as a research track.

**Exit criteria:** deliberately not fixed yet; defined at Phase 2 exit with the
community, from Phase 2 data.

**Standing kill criterion for the whole project:** if at any point a centralized,
non-revocable, genuinely open alternative makes Sanad redundant (a real "public AI
utility" at scale — publicai.co is the closest existing effort), we fold our work into
it rather than compete. The mission is access, not the brand.

## Research tracks (parallel, publishable)

Run alongside all phases; each is an open problem we cite rather than claim:

1. **Activation privacy.** Today a pipeline peer can reconstruct prompts from
   activations (>90%, arXiv 2503.09291). Directions: deeper split points, noising,
   TEE-hosted stages. Any real result is publishable independently of Sanad.
2. **Cheap verification.** From TOPLOC-style detect-and-eject toward cheaper, lower-trust
   audit (VeriLLM, arXiv 2509.24257, and successors). Goal: shrink the trusted-verifier
   requirement, honestly documented at every step.
3. **WAN latency hiding.** Lookahead/speculative decoding across pipeline hops,
   prefill/decode disaggregation across mismatched hardware, RTT-aware pipeline
   assembly. The gap between ~9 tok/s (WAN, today) and usability is the product's
   ceiling; every improvement here is general and publishable.

## Non-goals (all phases)

- **No token, no crypto, no speculation.** Sanad points are non-tradeable by ToS,
  forever. This is a load-bearing legal and social choice, copied from AI Horde.
- **No "cheaper/faster API" positioning.** Centralized free tiers (Cerebras: 1M
  tokens/day at ~2,100 tok/s, August 2026) win that fight; pretending otherwise killed
  Petals.
- **No privacy claims** until the privacy research track delivers something real.
- **No training** in v0–v2; inference only. Distributed training is a different problem
  with a different (and crowded) landscape.
- **No new inference engine.** Adapters over Parallax/BloomBee/llama.cpp; upstream
  contributions welcome, forks a last resort.
