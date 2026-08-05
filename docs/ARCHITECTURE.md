# Sanad v0 Architecture

> Status: DRAFT / RFC. A first implementation of the coordinator/node/ledger design
> below now runs in [net/](../net/) (see [PROOF.md](PROOF.md)); the federation,
> audit, and privacy sections remain design-stage. All external claims are
> "as of August 2026" and cited. Corrections welcome — file an issue.

Sanad combines two proven-but-never-combined ideas:

1. **Pipeline sharding across volunteer nodes** — each worker hosts a contiguous slice of
   a large model's layers and requests flow through a chain of workers. Proven by Petals
   (arXiv 2312.08361), whose public swarm nevertheless died (last commit September 2024;
   `health.petals.dev` unreachable as of August 2026) — in large part because it had no
   incentive layer.
2. **Non-tradeable credit fairness** — contribute compute, earn credits, get priority when
   you consume; credits can never be bought or sold. Proven by AI Horde's kudos system
   (Haidra), alive and serving real traffic in August 2026 — but AI Horde workers must
   host *full* models, so it cannot serve models bigger than one volunteer's machine.

Sanad's contribution is the **network and fairness layer**, not a new inference engine.
Everything below is designed so that the inference-heavy parts are delegated to existing
engines through a pluggable backend interface.

---

## 1. Roles

### Client
Sends inference requests, streams tokens back. Runs the tokenizer, embedding layer, and
LM head locally (as Petals did), so raw token IDs never leave the client — note that this
does **not** protect the prompt from activation-inversion attacks; see §5. Clients may be
anonymous (lowest priority) or registered (spend earned credits for priority).

### Worker
Hosts a **contiguous layer range of one model** (e.g. layers 24–47 of a 70B model) via a
backend adapter (§3). Registers with the coordinator, heartbeats, executes pipeline-stage
jobs, and earns credits for verified useful tokens served. A machine may run multiple
worker processes for different models/ranges if it has the memory.

Rule-of-thumb capacity math (from the August 2026 landscape survey): usable pool ≈ 0.7 ×
summed device RAM after OS/runtime/KV overhead; GGUF Q4 ≈ 0.55–0.6 GB per billion
parameters; every worker must hold at least one full layer.

### Coordinator
**Centralized-but-open in v0 — stated plainly.** Like AI Horde's central server, the v0
coordinator is a single service (open source, Apache-2.0, anyone can self-host their own
network) that handles:

- **Discovery & registry** — which workers exist, which models/layer ranges they hold.
- **Pipeline assembly** — composing a full pipeline (layers 0..N covered contiguously)
  from available workers, preferring low inter-hop RTT.
- **Credit ledger** — the sanad-points database (§4). Append-only, periodically published
  in signed exports so the community can audit it and migrate away from any misbehaving
  operator.
- **Health & scheduling** — heartbeat tracking, queue management, priority ordering,
  failure re-routing.

Federation of coordinators (multiple mutually-recognizing instances) is a later-phase
goal, not a v0 feature. We say this openly because pretending to be decentralized while
running one server is exactly the kind of dishonesty that erodes projects like this.
A single open coordinator is how AI Horde has survived since 2022; it is a pragmatic,
proven starting point, and the single point of failure it creates is on the roadmap
(see ROADMAP.md, Phase 3).

---

## 2. Inference plane: pipeline sharding

Sanad uses **pipeline (layer) sharding**, not tensor parallelism. The reason is wire
physics: pipeline sharding moves one hidden-state vector per token per stage boundary
(~14 KB for a 7168-dim fp16 vector), which tolerates internet latency; tensor parallelism
requires multiple synchronizations per layer and is LAN-only in practice
(cf. distributed-llama, which needs low-latency Ethernet and 2^n nodes).

**Honest performance envelope.** WAN round-trip time caps single-stream decode speed. The
best published measurement we know of (arXiv 2602.16760, Feb 2026) achieved **8.7–9.3
tok/s for a 7B model over ~80 ms RTT links even with lookahead decoding**, projecting
15–19 tok/s at 20 ms RTT. A free centralized tier (e.g. Cerebras' 1M tokens/day at
~2,100 tok/s on 70B-class models, August 2026) beats any swarm on single-stream speed by
orders of magnitude. Sanad does not compete on interactive latency. It competes on:
(a) serving models too big for any one volunteer machine, (b) aggregate batched
throughput (Parallax demonstrates 131 tok/s batched vs 22 tok/s single-stream on the same
2-GPU pipeline), and (c) not being revocable by any company's free-tier policy.

### Pluggable backend interface

Sanad does **not** reinvent inference. A worker wraps an existing engine behind a small
adapter interface:

```
Backend adapter contract (conceptual):
  load(model_id, layer_range, quant, device_opts) -> ready | error
  forward(session_id, stage_input: tensor, kv_ref) -> stage_output: tensor
  metrics() -> {vram_used, tok_latency_p50, kv_slots_free}
  unload()
```

Candidate adapters, in current order of interest:

| Engine | Why | Caveats (honest) |
|---|---|---|
| **llama.cpp RPC** | Lowest-friction path; huge model coverage; CPU+GPU | Upstream describes it as *"fragile and insecure. Never run the rpc-server on an open network"* — proof-of-concept, no encryption/auth, identical builds required on all nodes. Usable for LAN prototypes only unless wrapped in an authenticated tunnel, which Sanad's worker shim must provide before any WAN use. |
| **Parallax** (GradientHQ, Nov 2025) | The most credible Petals successor: P2P pipeline parallelism with continuous batching and paged KV per stage; NVIDIA (SGLang/CUDA) + Apple Silicon (MLX); 5.3× better inter-token latency than Petals on like hardware | Young project; scheduler and network assumptions may not match a credit-mediated public swarm; needs upstream conversation. |
| **BloomBee** (UC Merced PASA Lab / Yotta Labs) | Direct Petals descendant (Hivemind + FlexGen lineage) built for exactly this topology; active in 2026 (GQA, Qwen3, Gemma support) | Research framework; no public swarm, no hardening; we would be its first incentive layer. |

Also on the radar as prior art and possible future adapters: prima.cpp (ICLR 2026,
arXiv 2504.08791) for heterogeneous low-RAM home clusters, exo for owner-trusted LAN
meshes, and distributed-llama for LAN tensor parallelism. Sanad stands on all of these
shoulders and says so.

The adapter boundary is the load-bearing design decision: if one engine stalls (as Petals
did), Sanad swaps engines without changing the network, ledger, or trust layers.

---

## 3. Request lifecycle (wire sketch)

```mermaid
sequenceDiagram
    participant C as Client
    participant K as Coordinator
    participant W1 as Worker A (layers 0-27)
    participant W2 as Worker B (layers 28-55)
    participant W3 as Worker C (layers 56-79)

    C->>K: POST /v0/jobs {model, max_tokens, priority_key?}
    K->>K: queue by credit priority; assemble pipeline
    K-->>C: {job_id, pipeline: [W1,W2,W3], session_token}
    C->>W1: open session (session_token), send embedded prompt activations
    W1->>W2: stage output (hidden states)
    W2->>W3: stage output
    W3-->>C: final-layer hidden states (client applies LM head, samples)
    C->>W1: next-token activation (decode loop, session pinned)
    Note over W1,W3: loop until stop condition
    C->>K: POST /v0/jobs/{id}/complete {tokens, per-stage receipts}
    K->>K: verify receipts (spot-audit hooks), credit each worker
```

Notes:

- **Session pinning.** The pipeline chosen at assembly time is pinned for the whole
  session (all decode steps hit the same workers). This is required for KV-cache
  locality and is also a privacy mitigation (§5): a prompt is exposed to one fixed,
  known set of operators, not a rotating cast.
- **Failure handling.** If a worker dies mid-session, the coordinator re-assembles the
  missing stage; the KV cache for that stage is lost and must be re-prefetched (prefix
  re-computation). This is the honest cost of volunteer churn; Petals paid it too.
- **Streaming.** Tokens stream client-side as the client samples them; workers never see
  output token IDs, only hidden states.

---

## 4. Credit ledger: sanad points

Directly modeled on AI Horde's kudos, the one incentive design in this space that has
demonstrably kept a volunteer inference network alive for years.

**Earning.** Workers earn points per **verified useful token served**:

```
points = tokens_processed × model_class_weight × latency_tier_weight
```

- `model_class_weight`: serving a layer-slice of a 70B model earns more per token than a
  7B slice (scarcer capacity, more memory committed).
- `latency_tier_weight`: workers meeting the published p50 stage-latency target for their
  tier earn full weight; chronically slow stages earn reduced weight (they slow the whole
  pipeline).
- A small uptime trickle for workers holding **scarce layer ranges** of in-demand models
  keeps pipelines assemblable during quiet hours. Weights are published constants,
  tunable by open governance, never by hidden code.

**Spending.** Points buy **queue priority only**. They are not money, not a claim on
anything, and never expire. Anonymous use is allowed and served within bounded time —
every third queue slot is strictly first-come-first-served regardless of credits
(anti-starvation), the rest go to contributors first. Like AI Horde, Sanad must be
usable by people who cannot contribute.

**Non-tradeable by Terms of Service.** Copying AI Horde's proven rule verbatim in
spirit: points may be gifted, but buying or selling them is banned and enforced by ToS.
No token, no crypto, no speculation — and consequently no securities-law surface: points
are earned recognition for delivered work, never sold, never marketed as an investment.

**Ledger mechanics (v0).** Plain append-only rows in the coordinator's database (schema
below), with periodic signed public exports. Not a blockchain; deliberately so.

---

## 5. Trust and privacy — the honest section

### Trust model in v0: permissioned, and we say so

v0 workers are **known, registered operators** — real identities (or at least
long-lived pseudonymous accounts vetted by the community), manually approved. This is a
permissioned network at launch. We state that plainly because the alternative — claiming
open trustless membership we cannot verify — would be false.

The roadmap to open membership is **spot-audit verification**, following TOPLOC
(arXiv 2501.16007): locality-sensitive hash commitments over top-k activations
(~258 bytes per 32 tokens, validation ~100× faster than re-inference), extended in
TOPLOC v2 to pipeline-parallel settings with group verification plus stage-by-stage
blame assignment — battle-tested across 1,250+ permissionless community GPUs in Prime
Intellect's SYNTHETIC-2. Sanad's job/receipt schema (§6) carries commitment fields from
day one so audit hooks can be turned on without a protocol break.

Be clear about what this buys: TOPLOC-style auditing is **detect-and-eject**, requiring
a trusted verifier that can re-execute — it is *not* a cryptographic proof of honest
computation. zkML at 100B+ scale remains impractical (as of August 2026). Fully
trustless anonymous inference is an open research problem (see also VeriLLM,
arXiv 2509.24257), and Sanad does not claim to have solved it.

**Sybil notes.** Without payment or stake, identities are cheap. A points system invites
Sybil farming (fake workers "serving" fake jobs to mint priority). v0 sidesteps this via
registration; later phases mitigate via audit-gated onboarding, earning rate-limits on
young identities, cross-checking served tokens against real client demand, and the fact
that points buy only priority — a Sybil attack on Sanad yields queue position, not money,
which caps the attacker's incentive. Capped, not eliminated: this remains a listed risk.

### Privacy: activations leak to peers today

Stated without hedging: **a worker in the pipeline can substantially reconstruct your
prompt from the activations it processes.** arXiv 2503.09291 (CCS 2025) demonstrates
>90% prompt reconstruction from intermediate activations with auxiliary data. Running
the embedding layer client-side (as Sanad and Petals do) does not prevent this.

v0 mitigations — honest about being mitigations, not solutions:

- **Trusted-operator tier.** In v0 every worker is a registered, vetted operator, and
  clients can restrict sessions to operators they explicitly trust.
- **Per-session pipeline pinning.** One session's activations are exposed to one fixed
  set of operators, never rotated mid-conversation.
- **No logging covenant** in the operator agreement, with spot checks — a policy control,
  not a technical one, and labeled as such.

Research agenda (tracked openly, not promised): deeper split points (the Feb 2026 WAN
study measured ~59% token recovery at a 2-layer split vs ~35% at 8 layers — depth helps
but does not solve), activation noising, TEE-hosted stages, and client-side partial
computation. Until one of these lands, Sanad's guidance to users is blunt: **do not send
prompts through the swarm that you would not show the pipeline's operators.** For private
work, run a local model; Sanad is public infrastructure for public-scale models.

---

## 6. Data shapes (v0 wire sketches)

Illustrative JSON, not final. All endpoints versioned under `/v0/`.

**Worker registration** — `POST /v0/workers/register`

```json
{
  "worker_id": "w_7f3a...",
  "operator_account": "op_alwabel",
  "backend": { "engine": "parallax", "version": "0.4.2" },
  "models": [
    {
      "model_id": "qwen3-32b-q4",
      "layer_range": [0, 31],
      "quant": "q4_k_m",
      "kv_slots": 8
    }
  ],
  "net": { "endpoint": "wss://...", "region_hint": "eu-central", "measured_uplink_mbps": 40 },
  "pubkey": "ed25519:..."
}
```

**Heartbeat** — `POST /v0/workers/{id}/heartbeat` (every ~20 s)

```json
{
  "ts": "2026-08-05T12:00:00Z",
  "status": "serving",
  "load": { "active_sessions": 2, "kv_slots_free": 6, "stage_latency_p50_ms": 38 },
  "vram_free_mb": 3100
}
```

**Job** — created by client, annotated by coordinator with the assembled pipeline:

```json
{
  "job_id": "j_01H...",
  "client": "anon | acct_...",
  "model_id": "qwen3-32b-q4",
  "max_tokens": 1024,
  "priority": 1250,
  "pipeline": [
    { "stage": 0, "worker_id": "w_7f3a", "layers": [0, 31] },
    { "stage": 1, "worker_id": "w_c21d", "layers": [32, 63] }
  ],
  "session_token": "st_...",
  "audit": { "commitment_scheme": "toploc-v2", "sample_rate": 0.05 }
}
```

**Per-stage receipt** — submitted at completion; the hook for spot audits:

```json
{
  "job_id": "j_01H...",
  "stage": 1,
  "worker_id": "w_c21d",
  "tokens_processed": 812,
  "activation_commitments": "b64:...",
  "wall_ms": 61400,
  "signature": "ed25519:..."
}
```

**Credit ledger entry** — append-only:

```json
{
  "entry_id": "l_9k2...",
  "ts": "2026-08-05T12:01:03Z",
  "account": "op_alwabel",
  "delta": 974,
  "reason": "serve",
  "job_id": "j_01H...",
  "calc": { "tokens": 812, "model_class_weight": 1.5, "latency_tier_weight": 0.8 },
  "prev_hash": "sha256:..."
}
```

(`prev_hash` chains entries so published ledger exports are tamper-evident; again, not a
blockchain — a signed append-only log.)

---

## 7. What Sanad is not

- **Not a cheaper or faster API.** That positioning killed Petals; free centralized
  tiers win on speed and will for the foreseeable future. Sanad is public
  infrastructure — BitTorrent + Wikipedia for AI inference — valuable for resilience,
  for models too big for one machine, and for independence from revocable free tiers
  and geo-blocks.
- **Not private.** See §5, until the research agenda says otherwise.
- **Not trustless.** Permissioned in v0, spot-audited later, cryptographically verified
  never-yet (honestly: an open problem).
- **Not a new inference engine.** Engines are adapters; Sanad is the network, the
  fairness ledger, and the operational glue on top of Petals' and AI Horde's and
  Parallax's and BloomBee's shoulders.
