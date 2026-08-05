# Contributing to Sanad

Thank you for looking at this. First, an honest statement of where things stand.

## Where the project is (read this first)

Sanad is young, but no longer hypothetical. On 2026-08-05 the first two
milestones shipped with recorded proofs ([docs/PROOF.md](docs/PROOF.md)):
**v0.1 First Light** — a real model's layers physically split across two node
processes over TCP, credits earned by serving — and **v0.2 The Living
Network** — a capacity ladder, a polite node that yields to its owner, and
layer-share-weighted credits. The real network layer lives in [`net/`](net/)
and is Windows-first for now. Beyond that, what exists is a design (an RFC and
concept documents), a small prototype simulator, and a set of hard open
problems that we name plainly instead of hiding. Everything so far has run on
one physical machine with trusted nodes and small models; there is still no
network you can join from home.

We are looking for **founding contributors**: people who want to argue the
design into shape, do the research, and build the next working pieces.
Contributions are currently most valuable in four places: multi-machine
testing, a Linux/macOS port of `net/` (binary names are hardcoded with `.exe`
and the busy sensor shells out to PowerShell), tests, and review of the
upcoming human-loop RFC. If you join now, you are joining a project on day
one — with everything that implies, good and bad.

You do not need a GPU, and you do not need to be a machine-learning researcher.
The most valuable early contribution is careful, critical reading.

## Ways to contribute right now

### 1. Discuss the RFC

The core design lives in this repository's docs. Open a GitHub Issue or a
Discussion thread on `A-Alwabel/sanad` and tell us:

- where the design is wrong, unclear, or underspecified;
- where a cited number does not support the claim built on it;
- prior art we missed. Sanad deliberately stands on the shoulders of
  Petals (arXiv 2312.08361), Hivemind, AI Horde's kudos system, Parallax,
  BloomBee, prima.cpp (arXiv 2504.08791), distributed-llama, exo, TOPLOC
  (arXiv 2501.16007), and the Public AI Inference Utility — if there is
  relevant work we have not credited, that is a bug. File it.

Disagreement is welcome. "This will not work, and here is why" is a
first-class contribution at this stage.

### 2. Review the docs

Read `README.md`, `docs/CONCEPT.md`, and the rest of `docs/`. Every factual
claim in this project is supposed to carry a source and a date ("as of
August 2026"). If you find a claim without one, or a claim its source does not
support, open an issue. The project's credibility rests on this discipline.

### 3. Run the prototype simulator

The Phase 0 simulator is pure Python (standard library only — no
dependencies to install). See the README for the entry point. Useful
contributions:

- reproduce the reported numbers and report your environment;
- break it, and file a minimal reproduction;
- extend the latency/churn models with measured WAN data rather than
  assumed constants.

### 4. Claim a research track

Three problems gate everything else. Each is open or only partially solved,
and each can be worked on today with no network in existence. If you want to
own one, open an issue titled `[track] <name>` and say what you plan to try.

**Privacy (open problem).**
Activation privacy through untrusted peers is *unsolved*. arXiv 2503.09291
(CCS 2025) reconstructs input prompts from intermediate activations with
>90% accuracy given auxiliary data. No deployed consumer P2P system protects
against this today. Directions worth exploring: deeper split points (the same
line of work measured ~59% token recovery at a 2-layer split vs ~35% at
8 layers), activation noising, TEE-assisted stages, and — most immediately
useful — a reproducible evaluation harness so proposed defenses can be
measured instead of asserted. Until this is solved, Sanad's docs must (and do)
tell users that prompts routed through the swarm are not private.

**Verification (partially solved).**
TOPLOC (arXiv 2501.16007) verifies inference from untrusted workers via
locality-sensitive hashes of activations, and its pipeline-parallel v2 adds
group verification with stage-level blame assignment — battle-tested on
1,250+ community GPUs. But it is detect-and-eject auditing that requires a
trusted re-executing verifier, not a cryptographic proof. Track work: adapt
TOPLOC-style verification to a volunteer swarm with no central verifier of
last resort; study collusion; follow VeriLLM (arXiv 2509.24257) and related
work.

**WAN latency (the hard physical cap).**
Pipeline sharding moves only ~10–30 KB of hidden state per token per stage
boundary, so bandwidth is not the problem — round-trip time is. arXiv
2602.16760 measured 8.7–9.3 tok/s for a 7B model over ~80 ms RTT links even
with lookahead decoding. Sanad does not promise to beat this; we design
around it. Track work: speculative/lookahead decoding in a swarm setting,
geography-aware chain planning (keeping a pipeline's stages in one region),
and batching for aggregate throughput where single-stream speed is capped.

### 5. Adapter work

Sanad does not intend to write a new inference engine. The plan is a thin
network/incentive layer over existing runtimes. We need people to evaluate and
prototype adapters for:

- **llama.cpp RPC** — the lowest-friction way to pool mismatched machines,
  but upstream describes it as proof-of-concept, "fragile and insecure",
  with no encryption or authentication. An adapter must add a transport
  security layer, not just wrap it.
- **Parallax** (GradientHQ, launched Nov 2025) — P2P pipeline parallelism
  with continuous batching and paged KV; the most credible current successor
  to Petals for mixed NVIDIA/Apple swarms.
- **BloomBee** (UC Merced PASA Lab et al.) — the most active Petals
  descendant; Hivemind-based, actively developed, but with no public swarm
  or incentive layer — exactly the gap Sanad wants to fill.

Also worth tracking for ideas or future adapters: prima.cpp, distributed-llama,
and exo. Comparative write-ups ("I ran X across two homes and here is what
actually happened, with numbers") are extremely valuable.

## Code guidelines

- **Language:** Python 3.10+.
- **Dependencies:** Phase 0 code is **standard library only**. Adding a
  third-party dependency requires an issue and agreement first.
- **Tests are required.** Every PR that changes code includes tests
  (`unittest` from the stdlib). PRs without tests will be sent back — not as
  a formality, but because a project making claims about correctness of
  distributed computation cannot have an untested codebase.
- **Style:** readable over clever. Match the existing style. Small, focused
  PRs; every changed line should trace to a stated purpose.
- **Docs:** factual claims cite a source and carry a date. No hype — if a
  sentence would look at home in a marketing deck, rewrite it.

## Process

1. For anything larger than a typo fix, open an issue first and outline what
   you intend to do.
2. Fork, branch, make the change, include tests, open a PR that links the
   issue.
3. Contributions are accepted under the project license, Apache-2.0
   (inbound = outbound).

All project interaction is covered by our [Code of Conduct](CODE_OF_CONDUCT.md).

## Questions

Open an issue. There is no chat server, mailing list, or Discord yet — public,
searchable issues and discussions are the project's memory, and at Phase 0 we
want every design conversation on the record.
