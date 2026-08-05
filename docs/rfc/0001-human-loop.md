# RFC 0001: The Human Loop

- **Status:** Draft — open for community review
- **Created:** 2026-08-05
- **Target release:** v0.3
- **Scope of this RFC:** feedback collection, integrity, credits, and data governance. Training is **explicitly deferred** — see [Training path](#training-path-deferred).

---

## Summary

v0.3 lets people who use Sanad tell the network which answers were good. Thumbs up or down on any served answer; optionally, a side-by-side duel between two candidate answers. Honest feedback earns a small number of credits — the same non-tradeable, priority-only credits that serving earns, under the same founding commitment: never buyable, never sellable, forever.

The feedback accumulates into a community-owned, openly licensed preference dataset, released with the methods used to collect it. One day that dataset may post-train the small models this network serves, through a public, PR-gated process where humans approve every change. **That day is not in v0.3.** v0.3 builds the collection pipeline and its defenses, because the evidence says the defenses cannot be retrofitted. Depth before speed.

## Motivation

Sanad exists because open knowledge that depends on a vendor's goodwill is not open. The same is true of the human feedback that shapes models. The largest preference-collection operation in the world, LMArena, gathered its votes from volunteers, pays them nothing, releases only a fraction of the data (~20%, retaining the rest as a strategic asset), and reached a $1.7B valuation on it (Series A, Jan 2026). The best community datasets that do exist are either finished (OpenAssistant, shut down Oct 2023) or lab-produced (NVIDIA HelpSteer). Nathan Lambert's RLHF book states the gap plainly in its 2026 edition: *"As of 2026, there are no open models with fully open human preference data released with the methods used to collect it"* (rlhfbook.com, Preference Data chapter).

Sanad is positioned to fill exactly that gap, because it has something no labeling site has: raters who are also users of a live service, with a credit they already want (queue priority), inside a project whose data and governance are public by construction.

What Sanad would be first to do is the **combination**, not any piece: one non-tradeable credit earned by both serving compute and rating answers; community-owned preference data released with collection methods; the loop closing onto the network's own models; public PR-gated promotion. AI Horde ran kudos-for-ratings in 2023 (images, external dataset, lapsed). Tensorplex Dojo closed the feedback-to-model loop in Jul 2025 — with tradeable tokens and an all-rights-reserved model. We claim the combination carefully, and we inherit the precedents' failures honestly, starting with the most important one:

**Every precedent that attached any reward to ratings was botted within days.** AI Horde's kudos were explicitly worthless — non-tradeable, and the service is free without them — and people still built bots to rate randomly, "almost immediately," poisoning the collection (db0, dbzer0.com, Jan–Mar 2023). Sanad's credits buy queue priority, a real scarce benefit. Our incentive to cheat is strictly stronger than the closest precedent's. This RFC is therefore mostly about integrity, and only secondarily about features.

## Prior art and lessons

| Precedent | What it proved | What it teaches Sanad |
|---|---|---|
| **AI Horde × LAION ratings** (Jan–May 2023; dbzer0.com, laion.ai) | Non-monetary credits recruit raters fast (~130k images in week one) | Botted "almost immediately" despite kudos being worthless; captcha collapsed volume, revealing prior fraud; lasting mitigation was per-rating trust metadata + post-hoc filtering, not prevention. Dataset card (Haidra-Org/AI-Horde-Ratings, 3.92M ratings, CC-BY-SA-4.0) spans Jan 2023–Apr 2026; whether collection still runs in Aug 2026 is unverified. |
| **OpenAssistant** (2023; arXiv 2304.07327) | 13,500 volunteers can produce RLHF-scale data (161k messages, 461k ratings) | Deferred rewards after peer review, multi-review, spam auto-removal, a "Trollboard" — and it still died of **moderation overhead** (Oct 24, 2023), with 89.1% male / median-age-26 skew baked into everything trained on it. |
| **LMArena / Chatbot Arena** | Pairwise voting scales without paying voters | Verbosity/markdown bias is structural (style-control regression added late 2024); rankings rigged with **hundreds** of votes out of 1.7M (arXiv 2501.17858, ICML 2025); model anonymity breakable by a simple classifier; "The Leaderboard Illusion" (arXiv 2504.20879) documents private-variant cherry-picking and data-access asymmetry. |
| **Tensorplex Dojo** (Bittensor SN52, Jul 2025) | The full feedback→DPO→model loop works in a decentralized network | ~3M raw feedback tasks distilled to only 12.5k DPO rows; tradeable-token rewards; corporate-owned output. Their synthetic-ground-truth probes with obfuscation are worth copying. |
| **Sahara / Vana** (2025–26) | "Community-owned AI" is contested marketing territory | Sahara migrated from non-tradeable points to tradeable tokens — monetization pressure is real and must be constitutionally blocked. Ownership must mean license + methods + governance, not rhetoric. |
| **HF/Argilla Data-Is-Better-Together** (2024–25) | Short sprints with dashboards work (~350 annotators, 10k prompts in days) | A fallback mode if an always-on economy proves too expensive to moderate. Reusable tooling and schemas. |
| **PRISM** (arXiv 2404.16019, NeurIPS 2024) | Demographic representativeness is achievable | Only with money (1,500 paid, profiled participants). v0.3 measures skew; it does not fix it. |
| **HF Community Evals** (2026) | PR-gated, reproducibility-badged community evaluation is a workable governance pattern | Live precedent legitimizing Sanad's PR-gated model-change flow. |

## Threat model

Because credits are non-tradeable, the rational attacker's ultimate payoff is not credits. It is **write access to the future training set**. The protected resource of v0.3 is "preference signals admitted to the dataset," not "credits earned." The rating channel is treated as adversarial by default.

- **T1 — Credit-farming bots.** Random or scripted ratings to farm queue priority. Certainty, per AI Horde. Defenses: eligibility gates, captcha, caps, vesting, gold probes (Design §Credit rules).
- **T2 — Sybil rings and collusion.** Accounts that agree with each other to farm agreement-weighted trust. Theory says no *symmetric* reputation function is sybilproof (Cheng & Friedman 2005); production systems have been flipped with <10 coordinated ratings (Community Notes analysis, arXiv 2604.11224, Apr 2026). Defense: trust flows asymmetrically from serving operators outward; per-subnet caps; retroactive purge.
- **T3 — Preference poisoning aimed at the model.** Verified 2026-08-05: 0.5% poisoned pairs embed backdoors via DPO and 0.3% adversarially chosen label flips suffice (arXiv 2406.12091, AAAI); 1–5% injection steers target entities/sentiment (Best-of-Venom, arXiv 2404.05530); dose-response is log-linear (PoisonBench, arXiv 2410.08811); the feedback channel itself is a demonstrated injection vector (arXiv 2507.02850). PR reviewers cannot see distributed poison in an aggregate diff — this is why training is deferred and why every rating stays attributable and purgeable forever.
- **T4 — Style gaming.** Verbose, markdown-heavy answers win at equal content quality (LMArena's documented bias). Not malicious, but it leaks straight into training if unlogged. Defense: log style features per vote from vote one.
- **T5 — Operator self-rating.** A Sanad-specific conflict no precedent had: node operators rating their own outputs up or rivals' down. Assume UI anonymity is breakable (classifiers de-anonymize LMArena models easily). Defense: never route a rating task to an account linked to the serving node; audit rater–node correlation.
- **T6 — Economic distortion.** If rating out-earns serving per hour, click-farming beats GPUs and operators defect; conversely, tiny per-click payments can crowd out intrinsic motivation (Gneezy & Rustichini 2000). Defense: hard issuance caps and instrumented tuning; there is no literature on sizing non-monetary rewards, so every constant below is explicitly a guess that will be tuned in public.

**Accepted risks in v0.3:** demographic skew (measured, not corrected); residual fraud below detection thresholds (diluted by volume and filterable post-hoc via trust metadata, Haidra's model); the impossibility of perfect defense — like TOPLOC auditing, this is detect-and-eject, not cryptographic proof.

## Design

### What v0.3 ships — and what it does not

Ships: in-client feedback, duel mode, the integrity pipeline, capped vested credits, the open dataset with governance. Does **not** ship: training runs, model leaderboards, reward models, free-text feedback (moderation cost is what killed OpenAssistant), demographic quotas.

### Feedback

The primary signal is a single-response **thumbs up / thumbs down** on any answer served to you, offered in the client at the moment of delivery (rate-while-you-wait, AI Horde's proven placement — never a separate labeling destination). This shape is deliberate: it produces (prompt, completion, label) records — exactly the unpaired binary format KTO training consumes (arXiv 2402.01306), which tolerates label noise and thumbs-down-heavy imbalance far better than DPO. The rating UI is designed for the training method we can realistically run first, not the one that sounds best.

Every record stores: pseudonymous rater ID, timestamp, response latency, model/adapter hash, serving-node ID, position (for duels), response length, markdown-feature counts, and the rater's trust tier at time of rating. Style features are logged so LMArena-style style control can be applied at training time; this cannot be retrofitted.

### Duel mode

Optionally, a user may request a **duel**: two candidate answers to the same prompt, side by side, anonymized, position randomized, one vote (A / B / tie). Duels earn slightly more credit than thumbs because they cost more attention and yield DPO-grade pairs. Duel candidates come from different nodes and, where available, different models/adapters. A rating task is never routed to an account associated with either serving node (T5). We assume raters may guess which model produced an answer; randomization and the rater-model (below) limit, but do not eliminate, partisan voting.

Duels are **not** a leaderboard. Stable model rankings need thousands of votes per model and are riggable with hundreds (arXiv 2501.17858); Sanad will not have the volume, and will not pretend to. No public model ranking ships in v0.3.

### Credit rules — anti-gaming parameters

All constants below are initial values, published as governance constants, changeable only by public PR. They are guesses (there is no quantitative literature on non-monetary reward sizing); the commitment is to instrument and tune them in public.

| Constant | Initial value | Rationale |
|---|---|---|
| Eligibility gate | Account ≥ 14 days old **and** has earned ≥ 1 credit by serving, **or** completed a 50-rating unpaid apprenticeship at ≥ 80% gold accuracy | Ties the rating economy to the costly serving economy — the strongest cost-of-account Sanad has; the apprenticeship doubles as the sybil gate |
| Anonymous feedback | Accepted, stored at lowest trust tier, earns nothing | Mirrors serving policy (anonymous users always served, at lowest priority) and Haidra's anonymous = −50 |
| Proof of humanity | Captcha on rating endpoints, risk-triggered | Haidra's retrofitted fix, shipped up front |
| Daily credit-eligible ratings | First 25 per account per day; further ratings earn reputation only | Per-account cap; makes farming linear-bounded |
| Aggregate issuance cap | Rating-earned credits ≤ 10% of serving-earned credits per epoch, pro-rata scaled | Click-farming can never out-earn a GPU; protects serving operators' priority value |
| Tuning trigger | If rating credits/hour > 20% of serving credits/hour on commodity hardware, per-rating value is reduced | Instrumented kill-valve for economic distortion |
| Vesting | Credits vest T+72h, only after validation passes | OpenAssistant's deferred-reward pattern; never pay per click |
| Gold probes | ≥ 10% of shown items are seeded known-degraded variants (truncated, off-topic, wrong-language, or strong-consensus losers), generated per-session | Dojo's synthetic-ground-truth technique; per-session generation defeats collective gold memorization (Checco et al. 2018) |
| Gold threshold | Trailing accuracy < 80% over last 50 probes → vesting halts, account flagged | Detect-and-eject, consistent with TOPLOC philosophy |
| Swap consistency | ~5% of duels re-shown with sides swapped | Self-consistency probe feeding competence estimates |
| Speed floor | No vesting for responses under 40% of the rater's rolling median response time | Standard speeder threshold from survey research |
| Validation quorum | k ≥ 3 independent ratings per duel item before it is marked *validated* | Prerequisite for competence modeling |
| Rater model | MACE/Dawid-Skene competence estimation (Crowd-Kit), run in batch; bottom competence decile excluded from payout **and** dataset | Model-based aggregation reliably beats majority vote (Hovy et al. 2013); trust seeds asymmetrically from registered serving operators (Cheng & Friedman 2005) |
| Rate limits | Per-account and per-subnet daily caps | Cheap sybil friction |
| Clawback | Retroactive credit clawback + full dataset purge by rater ID | Stack Overflow's vote-reversal model; mandatory for T3 |

**Non-tradeability, restated.** Rating credits are the same credits, under the same founding commitment: non-tradeable by the Terms of Service, forever — no token, no speculation. Buying or selling credits zeroes the account (AI Horde's enforcement, adopted verbatim in spirit). Sahara's migration from points to tokens shows the pressure will come; reversing this commitment requires the same public PR-gated process as a model change, which is to say: it should never happen.

**Kill criteria for v0.3 itself.** If, after 90 days: gold pass-rates indicate < 50% of rating volume is human, or moderation load exceeds what maintainers can sustain, the always-on rating economy is suspended in favor of short DIBT-style sprints (days-long, dashboard-driven, concrete dataset goal). Moderation overhead — not funding — is what killed the best project in this space; we pre-commit to the fallback rather than burning out.

## Data governance

**Ownership, operationally defined.** "Community-owned" means: open license on the data, collection methodology published with every dataset version, contributor consent at rating time, and public governance over dataset releases and model promotion. Not a token (Vana/Sahara claim ownership through tradeable tokens; that is not this).

**License.** Proposed: **CC-BY-SA-4.0** for the dataset (matching Haidra-Org/AI-Horde-Ratings; share-alike prevents the LMArena-style enclosure this project positions against), **Apache-2.0** for any trained adapters/models (matching OASST). The choice is open for review — Apache-2.0/CC-BY on data would maximize reuse at the cost of permitting proprietary enclosure. Decided **before the first vote is collected**, in this RFC's resolution.

**Consent.** First rating requires a click-through consent covering: publication of the rating with trust metadata, publication of the associated prompt+completion pair, and the pseudonymous-attribution model. A per-rating **private flag** excludes the pair from the public dataset — and therefore from training, under the invariant: **Sanad trains only on published data.** Nothing enters a model that the community cannot audit.

**Privacy.** Prompts can contain personal information. Before any dataset release: automated PII scrubbing, a takedown process, and the private flag above. Coarse, optional, self-declared demographics (following PRISM's practice) are recorded so skew is measurable — OASST's 89% male skew went unmeasured into every downstream model; ours will at minimum be documented in every dataset card.

**Provenance and reversibility.** Every release is a versioned snapshot with a content hash. Every rating carries per-rating trust metadata (trust tier, account age, gold history, validation status) so downstream consumers can filter — Haidra's transparency-over-prevention posture, adopted as policy. When a fraud ring is identified post hoc, its entire history is purged and the next snapshot supersedes; any training run (future) must reference a snapshot hash, making poisoning auditable and rollbackable.

## Training path (deferred)

**v0.3 trains nothing.** This is a decision, not an omission. Three reasons, all evidenced:

1. **The data will not exist yet.** The practitioner floor for DPO is ~1k–5k well-curated pairs; Dojo needed ~3M raw feedback tasks to distill 12.5k DPO rows (Jul 2025). Promising a model before the data exists sets up a public failure.
2. **Small noisy sets are dangerous.** DPO on small sets overfits within 1–3 epochs and can silently regress capability; 0.5% poisoned pairs suffice for a backdoor (arXiv 2406.12091). Integrity machinery must run and be measured before its output feeds gradients.
3. **The curation pipeline is the product.** The core engineering artifact of the human loop is the filtering, competence-weighting, and provenance machinery — not the training run, which is commodity tooling.

**Intended stack (informational, not binding):** KTO first (TRL KTOTrainer; consumes our thumbs data natively, robust to noise/imbalance — arXiv 2402.01306), DPO later as a polish pass once ≥ 1k clean pairs exist. QLoRA on 0.5B–8B bases fits a single 8–16 GB consumer NVIDIA GPU (Unsloth benchmarks, 2025–26); CPU nodes serve and rate but do not train (llama.cpp's revived trainer is FP32 full-parameter and "very much WIP" as of Aug 2026 — llama.cpp is our serving layer, not our training layer). Adapter path: PEFT → convert_lora_to_gguf.py → llama-server `--lora` for A/B, merged-then-requantized GGUF for canonical releases. The gradient step runs on 1–3 designated trainer nodes but is **reproducible rather than trusted**: pinned dataset snapshot hash, seed, and config in the PR, so anyone with a 24 GB consumer GPU can re-run and confirm the adapter hash — the INTELLECT-2/Psyche division of labor, minus their unsolved distributed-verification problem. No GRPO/RL: it requires verifiable rewards human preferences cannot provide without an extra reward-model stage.

**Criteria to unfreeze training** (all must hold; verified in a separate future RFC-0002):

1. ≥ 5,000 validated single-response labels **or** ≥ 1,000 validated preference pairs surviving all filters;
2. < 10% of the validated set from accounts younger than 30 days;
3. Top-10 raters contribute ≤ 30% of the validated set; rater-influence Gini published;
4. Gold pass-rate of admitted raters stable ≥ 80% over the trailing 60 days;
5. The reproducible-training PR template is merged: snapshot hash, seed, config, adapter SHA-256, before/after eval deltas on a fixed harness, red-team canary prompts, and a rater-concentration data card;
6. A training RFC (RFC-0002) is approved through the normal public process. Every variant evaluated is published, not just the winner ("The Leaderboard Illusion" lesson, arXiv 2504.20879); no model is ever silently deprecated.

If any criterion fails, the training run does not happen. That sentence is the whole point of this section.

## Open questions for community review

1. **License:** CC-BY-SA-4.0 (share-alike, anti-enclosure) vs CC-BY/Apache-2.0 (maximal reuse) for the dataset?
2. **Recognition vs credits:** Given that a zero-value barnstar raised productivity ~60% (Restivo & van de Rijt 2012) while small payments can underperform none (Gneezy & Rustichini 2000), should v0.3 A/B an unpaid, recognition-only rating tier against the capped-credit design before committing?
3. **Always-on vs sprints:** Should the rating economy launch always-on with the kill criterion above, or begin as DIBT-style sprints and graduate to always-on?
4. **Anonymous ratings:** Keep at zero credit but include (flagged) in the dataset, or exclude from the dataset entirely?
5. **Duel candidate sourcing:** Same model on different nodes (tests serving fidelity) vs different models (tests model preference)? The former overlaps with existing auditing; the latter is more valuable for training but eases T5 partisan voting.
6. **Schema:** Adopt the oasst1/2 conversation-tree schema for interoperability, or a flatter KTO/DPO-native schema?
7. **Demographics:** Is optional self-declared coarse demographic collection acceptable to this community, or does the privacy cost outweigh the measurability benefit?
8. **Haidra alignment:** Should we approach db0/Haidra for shared policy language on non-tradeable credits and rating-fraud countermeasures? Same values, three years of directly relevant scar tissue, and firsthand data on whether a text-rating loop was ever attempted.

## References

- AI Horde ratings history: dbzer0.com — "A collaboration begins between Stable Horde and LAION" (Jan 2023); "State of the AI Horde" (26 Mar 2023; May 2023); laion.ai/blog/laion-stable-horde. Dataset: huggingface.co/datasets/Haidra-Org/AI-Horde-Ratings (CC-BY-SA-4.0; card spans Jan 2023–Apr 2026; Aug 2026 activity unverified). Kudos philosophy: dbzer0.com/blog/the-kudos-based-economy-for-the-koboldai-horde.
- OpenAssistant: arXiv 2304.07327 (NeurIPS 2023); shutdown Oct 24, 2023 (LAION-AI/Open-Assistant README; simonwillison.net/2023/Nov/4/open-assistant).
- Arena manipulation: arXiv 2501.17858 ("Vote Rigging," ICML 2025); arXiv 2504.20879 ("The Leaderboard Illusion," Apr 2025); LMArena response, news.lmarena.ai/our-response (May 2025).
- Preference poisoning (verified 2026-08-05): arXiv 2404.05530 (Best-of-Venom); arXiv 2406.12091 (0.5% DPO backdoor, AAAI); arXiv 2410.08811 (PoisonBench, log-linear); arXiv 2507.02850 (LLM Hypnosis, feedback-channel injection); arXiv 2605.02495 (efficient offline-RLHF poisoning).
- Crowd QC: Hovy et al., MACE (NAACL 2013, aclanthology.org/N13-1132); Dawid & Skene 1979; Checco et al. 2018 (gold-question attack); speeder threshold research (measuringu.com/speeder-research).
- Incentives: Gneezy & Rustichini, QJE 2000 ("Pay Enough or Don't Pay at All"); Ho et al., WWW 2015; Restivo & van de Rijt, PLOS ONE 2012 (barnstars); Anderson et al., WWW 2013 (badges).
- Sybil/collusion: Cheng & Friedman 2005 ("Sybilproof reputation mechanisms"); arXiv 2604.11224 (Community Notes gaming, Apr 2026); arXiv 2607.01824 ("Gaming Consensus," Jul 2026); Shnayder et al., EC 2016 (Correlated Agreement peer prediction); "Reputation Gaming in Stack Overflow," arXiv 2111.07101.
- Precedent systems: Tensorplex Dojo (tensorplex.ai blog, Jul 3 2025); Vana (vana.org); Sahara points→token migration (saharaai.com, 2025–26); PRISM (arXiv 2404.16019, NeurIPS 2024); HF/Argilla Data-Is-Better-Together (huggingface.co, 2024–25); HF Community Evals (huggingface.co/blog/community-evals, 2026); LMArena commercial status (TechCrunch, Jan 6 2026).
- Training stack: KTO, arXiv 2402.01306; INTELLECT-2, arXiv 2505.07291 (May 2025); Nous Psyche (nousresearch.com/nous-psyche); SAPO, arXiv 2509.08721 (Sep 2025); llama.cpp training status (examples/training README; PR #8669; RFC #15442, fetched Aug 2026); Unsloth docs (docs.unsloth.ai).
- The gap: Nathan Lambert, RLHF Book, 2026 edition, Preference Data chapter (rlhfbook.com).

---

*Uncertainty flags, kept honest: whether AI Horde's image-rating collection is still active in Aug 2026 is unverified (last primary evidence 2023; dataset card metadata extends to Apr 2026). LMArena's ~3,000–5,000-vote inclusion threshold circulates mainly in low-quality sources and is treated as unverified. Stack Overflow's per-day reputation cap was not confirmed from a primary source. Every credit constant in this RFC is a guess to be tuned in public — no quantitative literature exists on sizing non-monetary rewards.*