# SCIENCE.md — what the sciences say about this design

**Status:** first edition, August 2026. Written after six independent specialist reviews of the v0.3 code and documents: commons governance, mechanism design, queueing and fairness, P2P churn and reliability, LLM serving systems, and volunteer motivation / HCI. Each reviewer read `net/sanad_net/` directly.

**Read this document with one caveat in front of you: almost nothing in it has shipped.** This is the reading, the decisions taken from the reading, and an honest list of what remains undecided. Every claim below is marked:

- **[IN CODE]** — implemented and testable today.
- **[DECIDED]** — we have committed to it; it is not written yet.
- **[OPEN]** — the science is clear, our answer is not.
- **[REJECTED]** — recommended to us and deliberately not adopted, with the reason.

We publish it because Sanad asks to be held to its commitments in public, and because a design this opinionated should be able to say which of its opinions have evidence behind them and which do not.

---

## 0. The five things all six fields agreed on

Independent reviewers from six literatures converged on the same five defects. That convergence is the most useful result in this document.

1. **Ordering a queue by accumulated balance is not a priority scheme.** Queueing theory calls it static priority (Cobham 1954, *JORSA* 2(1):70–76) and notes the bottom class's wait diverges. Mechanism design calls it a rank dictatorship that elicits no information and makes hoarding dominant (Kash, Friedman & Halpern, EC 2007, Thm 6.2). Commons governance calls it congruence failure. HCI calls it the seed of a leaderboard. It is `coordinator.py:202`.

2. **Credits are minted from nothing and there is no conservation law.** `Ledger.earn()` creates, `Ledger.spend()` destroys, and neither is bounded (`ledger.py:94–114`).

3. **"Anonymous users are always served" without a quantity rule is open access, not a commons.** Hardin (1968) described open access; Ostrom (1990) showed commons are *bounded*. The `min(seq)` reserve lane at `coordinator.py:230` is capturable in its entirety by one client.

4. **Availability is not modelled anywhere.** The capacity ladder maximises pooled memory; reliability decays as `p^k`; the failure detector is a fixed 15-second TTL applied to a node that is *designed* to go silent.

5. **Sanad has ~30 unit tests proving the system works and zero measurements proving it is fast or fair.** Correctness is instrumented; behaviour is not.

---

## 1. Commons governance

### What the field says

Ostrom's *Governing the Commons* (1990) is an empirical correction to Hardin: enduring commons are bounded communities with monitored, graduated, locally-amendable rules. Cox, Arnold & Villamayor-Tomás (*Ecology and Society* 15(4):38, 2010) re-tested the principles across 91 cases and split three of them, giving eleven. Baggio et al. (*Int. J. Commons* 10(2):417–439, 2016, fsQCA over 69 cases) found congruence is "the linchpin … independent of the type of system," and that cases lacking rule congruence, accountable monitors, and graduated sanctions are unsuccessful.

Judged against the eleven-principle form, Sanad clearly satisfies **one** (4A, resource monitoring — heartbeats, shard map, `/status`, `/ledger`, fsync'd append-only JSONL with `audit()`), partly satisfies three (2A, 2B, 7), and lacks seven.

Two further results shape our sequencing. Communication beats punishment: a single one-shot communication opportunity raised average net yield from 21% to 55% of optimum, and self-designed sanctioning reached ~90% (Ostrom, Walker & Gardner, *APSR* 86(2):404–417, 1992); peer punishment *alone* raised neither cooperation nor yield (Cason & Gangadharan, *J. Theoretical Politics* 28(1):44–73, 2016); groups whose first experience of enforcement is unexplained penalty stop building enforcement at all — 46% built none (DeCaro et al., *PLoS ONE* 19(8):e0307832, 2024). And governance capture is the realistic attack on a small, high-value, informally-run commons: Croatian Wikipedia was held by a captured admin group from ~2011 to 2020, enabled by high perceived value, limited early bureaucratic openness, and a preference for personalistic organisation over rules constraining admins (Kharazian, Starbird & Hill, CSCW 2024, doi:10.1145/3637338). Sanad is currently the second and third conditions, with the first arriving the moment it works multi-machine.

### What we changed

- **[DECIDED]** Three membership classes as boundary rules, none requiring money: GUEST (unauthenticated, served from the guest lane), MEMBER (Ed25519 key; admitted by one serving shift of ≥4 GB-hours *or* two vouches), OPERATOR (a member running a node). This replaces the free-text `user` field (`coordinator.py:422`) and the self-declared `operator` field (`coordinator.py:484`), where Sybil and whitewash cost are both currently zero.
- **[DECIDED]** Guest quota and per-bucket max-min fair queueing inside the reserve — a bounded share rather than an unbounded claim. Over quota, a guest is ranked last among guests and **never refused**.
- **[DECIDED]** The ADICO rewrite of GOVERNANCE.md (Crawford & Ostrom, *APSR* 89(3):582–600, 1995), with an explicit "Or else" column and every statement labelled STRATEGY / NORM / RULE. Today the document contains zero enforceable rules in that grammar.
- **[DECIDED]** A graduated sanction ladder, a conflict-resolution panel drawn by lot, written promotion criteria, 12-month terms, and a Debian-style recall procedure — the recall path must exist before the conflict (O'Mahony & Ferraro, *AMJ* 50(5):1079–1106, 2007).
- **[DECIDED]** `rules.json`, signed, hash published on `/status`, amended by an operator vote weighted by trailing-90-day GB-hours with a 10% per-operator cap.
- **[DECIDED]** `docs/TRADEMARK.md` and `docs/OPERATOR_RISK.md`. The forfeit-the-name clause in GOVERNANCE.md §1 is currently unenforceable: Apache-2.0 §6 grants no trademark rights and no mark is registered or asserted.

### What stays open

- **[OPEN]** Federation. One coordinator is one capture point, one failure point, one rulebook. Ostrom's DP8 (nested enterprises) is what the multi-machine milestone should be *built on*, not added after — and it is also the answer to the WAN latency ceiling in §5. The design sketch is: a POOL is one coordinator plus its nodes plus a `rules.json` that must be a superset of commitments 1–3; `GET /federation` publishes `{pool_id, tier, pledged_mb, queue_depth, rulebook_hash, ledger_root, guest_quota}`; credits are **recognised, never transferred** — a visiting member's home balance is honoured at a discount for priority only, and the serving pool mints into its own ledger. No transfer path is created, so non-tradeability is preserved by construction. We have not decided whether to do this at v0.4 or later.
- **[OPEN]** Whether the operator communication channel (a per-pool Matrix room or pinned Discussion) is a documented expectation of the OPERATOR role or merely available. The evidence says build it *before* the sanction ladder.

### What we get wrong, in this field's terms

We confused **open** with **open-access**. Boundaries do not require money — a keypair, a vouch, a one-off serving shift, a per-ASN bucket are all non-monetary — and without them the rival layer is the configuration Hardin described, not the one Ostrom studied. Separately: our issuance rule rewards decoded tokens when the operator's real cost is resident GB-hours and electricity. That is a DP2A congruence failure, the principle the empirical record identifies as the linchpin, and it is the incentive gap that killed Petals, re-implemented.

---

## 2. Mechanism design

### What the field says

Sanad's credits are not a scrip system; they are a money-burning system with free minting. Kash, Friedman & Halpern (EC 2007, Cor. 5.1) prove that for any population there is a finite per-capita threshold `m_T` above which the *only* equilibrium is that nobody serves for credits. The real-world instance is the Capitol Hill Baby-Sitting Co-op (Sweeney & Sweeney, *J. Money, Credit and Banking* 9(1):86–89, 1977).

The constitution is defensible on rigorous grounds, and we should stop arguing it only on ethics. Hartline & Roughgarden (STOC 2008, Prop. 5.1 + Thm 5.2) show money-burning mechanisms are Θ(1+log(n/k))-surplus maximizers, while mechanisms with no payment instrument at all are Θ(n/k). At n=1000 pending and k=4 slots that is 5.6% versus 0.4% of full surplus — a 14× advantage for having a non-tradeable priority currency over pure FIFO, at a cost of only a logarithmic factor versus real money. Gorokh, Banerjee & Iyer (EC 2017; *Math. of OR* 46(4), 2021) give black-box conversions with vanishing efficiency loss *and* vanishing gains from misreporting in repeated settings like ours.

The deployed precedent is Feeding America: 200+ food banks moved from a need-ranked queue to a non-tradeable currency in 2005; reallocation raised the value of food by 21% (~$115M/yr), donations rose by over 100 million pounds, and small food banks obtained 72% more pounds per client (Prendergast, *JPE* 130(8), 2022). The monetary design is directly copyable and is exactly what we lack: shares issued by a published formula, and **all** shares spent redistributed by the same formula — a closed loop with zero net issuance.

One thing we already got right, and should defend: the layer-share split `tokens × n_layers/total_layers` (`coordinator.py:307–322`) is linear and sums to `tokens` regardless of how an operator splits their identity. That is Sybil-neutral in magnitude — the proportional-share property Levin et al. (SIGCOMM 2008) show is near-strategyproof, and the opposite of BitTorrent's threshold reciprocity, which BitTyrant gamed for a median 70% download gain (Piatek et al., NSDI 2007). Newcomers starting at zero is also correct.

### What we changed

- **[DECIDED]** Closed loop. Burnt escrow enters a `pool` instead of vanishing; every epoch the entire pool is paid out by accumulated weight. Guest work then **dilutes** the payout rather than inflating supply.
- **[DECIDED]** `CREDIT_CAP = 7 × median_job_tokens`, overflow spilling to the pool. The 7 is `log₂(1/ε)` at ε = 0.01 from Ashlagi, Kerimov, Tamuz & Zhao (*Management Science* 2026; arXiv:2405.12414, Thm 4.5).
- **[DECIDED]** A conservation invariant as a unit test: `Σ balances + pool == M` after any sequence of submit / settle / refund. That single test is what makes "credits are a closed system" checkable rather than aspirational, and it is the natural extension of `Ledger.audit()`.
- **[DECIDED]** Written into GOVERNANCE.md next to "credits are never for sale": *the coordinator's objective is minimising the zero-balance-served fraction M₀, never maximising credits burnt.* Naor (*Econometrica* 37(1):15–24, 1969, §6) proves a revenue-maximising toll collector under-serves relative to the social optimum. The two objectives look similar on a dashboard and diverge in exactly the direction that hurts the people Sanad exists for.
- **[DECIDED]** Quantitative defence of non-tradeability in GOVERNANCE.md, citing the Θ(1+log(n/k)) versus Θ(n/k) separation.

### What stays open

- **[OPEN]** Whether users declare an explicit bid or burn rate. Charging by a declared rate `φ_u` is what Kelly's theorem requires (see §3), but it adds a concept to a UI whose main audience is a stranger asking one question. We may ship a single published standard rate with an optional "urgent" tier and revisit.
- **[REJECTED]** Demurrage. Commons governance recommended a 30-day half-life on balances; HCI requires that no displayed metric decrease because an operator was away. The cap does the anti-hoarding work decay was for — every active account saturates, so tenure buys nothing — without ever reducing an absent operator's balance. Recorded as a deliberate rejection.

### What we get wrong, in this field's terms

We call it a credit system while implementing an uncapped mint. Every job served for a zero-balance user is pure issuance of `tokens` credits against zero burn, and by design that is the majority of our traffic. This is not scrip; it is seigniorage with no central bank. And ordering by wealth collects no information about which request is urgent, so it cannot improve allocative efficiency over FIFO in any state of the world — it only redistributes, while making hoarding a dominant strategy that reduces everyone else's welfare.

---

## 3. Queueing and fairness

### What the field says

Our "every 3rd queue slot is strictly FIFO" is a **throughput** reserve of 1/3, not a **delay** bound. The anonymous class is stable only while `ρ₀ < 1/3`. Concretely, on a 30 s/request pipeline with exponential service: 14 anonymous req/hr → 51 s mean wait; 22/hr → 106 s; 29/hr → 231 s; 36/hr → 810 s; **40/hr → unbounded** — while the network as a whole is still at ρ < 1 and reports itself healthy. Forty anonymous requests an hour is a few dozen users.

The reserve is also denominated in **requests**, not tokens. That is precisely the failure Deficit Round Robin was invented to fix (Shreedhar & Varghese, SIGCOMM 1995; *IEEE/ACM ToN* 4(3):375–385, 1996). A credit-rich user issuing 8k-token generations and an anonymous user issuing 100-token ones both consume "one slot"; the anonymous token share is `(1/3)·(L_anon/L̄)`, potentially an order of magnitude below the intended third.

The replacement is Kleinrock's accumulating priority queue (1964, *Nav. Res. Log. Q.* 11(3):329–341; distributions in Stanford, Taylor & Ziedins, *Queueing Systems* 77(3):297–330, 2014). Priority is `P_r(t) = b(u)·(t − arrival)`; credits set the **rate** `b ∈ (0,1]`, not a level. Because `P` grows without bound for every `b > 0`, starvation is impossible. Their Lemma 4.2 — high-rate customers become able to overtake at reduced rate `λ_c(1−b)` — gives closed forms:

```
E[W_anon]   = W₀ / [(1 − ρ_c(1−b))(1 − ρ)]
E[W_credit] = W₀ (1 − ρ(1−b)) / [(1 − ρ_c(1−b))(1 − ρ)]
```

These satisfy Kleinrock's conservation law exactly, reduce to FCFS at b=1 and to Cobham strict priority at b=0, and yield two corollaries from one dial. With **b = 1/3**: an anonymous user never waits more than 3× what they would on a network with no credits at all, *at any load and any credit mix*; and the maximum speedup credits can ever buy is `1/(1−ρ(1−b))` — 1.25× at ρ=0.3, 1.50× at ρ=0.5, 1.88× at ρ=0.7, 2.50× at ρ=0.9, asymptotically 3.00×. Credits are worth almost nothing when the network is idle and at most 3× when it is jammed. That is the right moral shape, and it is a theorem.

Human-readable form, which belongs in the constitution: **an anonymous request that has waited 90 seconds outranks every paying request that has waited less than 30 seconds.**

Three further results we are adopting. Kelly (1997, *Eur. Trans. Telecom.* 8(1):33–37): proportional fairness with respect to payments requires the charge to be a **flow** (credits per second while backlogged), not a stock — our escrow layer and our scheduling layer are currently two mechanisms sharing a currency. Kleinrock's conservation law (1965, *Nav. Res. Log. Q.* 12(2):181–192; multi-server extension Bolch, *Acta Informatica* 10:105–109, 1978): `Σ_k ρ_k W_k` is invariant under every non-clairvoyant work-conserving discipline, so priority is exactly zero-sum in load-weighted delay. And Bertsimas, Farias & Trichakis (*Operations Research* 59(1):17–31, 2011): the price of max-min fairness is `1 − 4n/(n+1)²` against `1 − (2√n−1)/n` for proportional — 67% versus 47% worst-case loss at n=10 — so **α = 1**, not max-min.

For the concurrent case, VTC (Sheng et al., OSDI 2024, arXiv:2401.00588) supplies the token-denominated share layer with proven bounds, and its Theorem 4.8 proves no work-conserving *non-preemptive* schedule can avoid a fairness error of at least `w_q·M`.

### What we changed

- **[DECIDED]** APQ replaces the balance comparator. `b_anon = 1/3` becomes a constitutional constant, published as the two faces of one number: anonymous users never wait more than 3× a credit-free network, and credits never buy more than a 3× speedup.
- **[DECIDED]** Token-denominated deficit accounting per client, with counters carried across idle periods (`c_u = max(c_u, min c_i)` on re-entry) rather than reset — resetting is what lets a burst client game the system.
- **[DECIDED]** Constitutional text: *"Queue priority is zero-sum. By Kleinrock's conservation law (1965), `Σ_k ρ_k W_k` is invariant under every scheduling rule this network may adopt. Credits do not create capacity; only pledged memory does. Credits only decide whose delay is whose."* We keep the "everyone wins" claim for the capacity ladder, where pooling really does create capacity nobody had, and drop it for the queue, where it is provably false.
- **[DECIDED]** Preemption at token boundaries using llama-server's slot save/restore (`--slot-save-path`, `POST /slots/{id}?action=save|restore`), requeuing with the **original** `arrival_ts` so preemption never resets the aging clock. Verify the endpoint against the pinned build before relying on it.
- **[DECIDED]** Five tests: saturating-whale (anonymous p99 stretch stays under the b=1/3 bound); long-generation (anonymous *token* share ≥ 1/3 — this one fails today by construction); isolation; counter-lift after idle; and a conservation-law replay.
- **[DECIDED]** Primary fairness metric is the **stretch distribution by credit tier**, headline = max anonymous stretch. Pass/fail chart: `E[T(x)]/x` against `1/(1−ρ)` (Wierman & Harchol-Balter, SIGMETRICS 2003) — anything above that line is provably unfair against the processor-sharing benchmark. Jain's index is reported but never leads: with 100 users and one fully starved it still reads 0.99.

### What stays open

- **[OPEN]** `w_q/w_p` — the relative cost of an output token versus an input token — is the single most important constant in any fair LLM scheduler, and we have never measured it. Every VTC bound is stated in terms of `U = max(w_p·L_input, w_q·M)`. It is rung-dependent and must be recalibrated on every ladder change.
- **[OPEN]** Whether output-length prediction is ever adopted. Size-based scheduling breaks the conservation law — it can *reduce* the invariant — which would make the constitutional zero-sum claim no longer exactly true. If we do it, we do it deliberately.

### What we get wrong, in this field's terms

We describe a throughput guarantee as an anti-starvation guarantee. There is no aging term anywhere in the mechanism: `priority` is frozen at submit (`coordinator.py:205`) and never recomputed, and the reserve serves the single globally-oldest job, so it degrades to 1/3-rate service for the whole starving population rather than a per-job guarantee. We cannot state a worst-case wait today, let alone prove one. And we talk about credits making the network faster for contributors, which is false in aggregate by a 1965 theorem.

---

## 4. Churn and reliability

### What the field says

A pipeline is a series system: availability is the **product** of node availabilities, so churn is the dominant failure mode and it compounds exponentially in `k`. At p=0.9: k=5 → 0.590, k=8 → 0.430, k=12 → 0.282. The operationally relevant number is survival over a generation: with a 2-hour median residual and k=8, a 25 s generation survives with p=0.981, a 400 s generation with 0.735, a 30-minute agentic run with 0.250. Equivalently, **chain MTBF = mean_residual/k**: with 2 h mean residual and k=8, the pipeline breaks every 15 minutes. That is the number that should drive the roadmap, and the first genuinely multi-machine run will read as "the system is broken" when it is behaving exactly as the product formula predicts.

Session lengths are **not** exponential. Stutzbach & Rejaie (IMC 2006) fit Weibull with shape k = 0.34–0.59 across Gnutella, Kad and BitTorrent — a *decreasing* hazard rate. That makes past uptime a free predictor of remaining uptime ("the median peer has a remaining uptime between 50% and 100% of its uptime so far"), and it makes **Longest Uptime provably the greedy-optimal non-oracle selection policy** (Godfrey, Shenker & Stoica, SIGCOMM 2006: "the same as Max Expectation when the underlying session time distribution has decreasing failure rate"). The same paper warns that Preference List strategies — always the top-k of a *static* ranking — are as bad as a fixed random set. Any policy that builds pipelines from "top-k by credits" or "top-k by pledged memory" inherits that pathology.

Uptime gating is the highest-leverage change available and costs zero bytes. P(k=8 chain survives 400 s): no gate 3e-5; 15-min gate 0.0072; 1-hour 0.292; 4-hour 0.735; 6-hour 0.814; 12-hour 0.902.

The polite node is a false-positive generator by construction, and this is the deepest design tension in Sanad. Chandra & Toueg (*JACM* 43(2):225–267, 1996) establish you cannot distinguish slow from crashed asynchronously. Our node runs at low OS priority and yields when its owner needs the machine — it is descheduled *precisely when it is healthy and willing* — and a fixed TTL will convict it. Lifeguard (Dadgar, Phillips & Currey, arXiv:1707.00788) measured that under plain SWIM a **single** CPU-starved machine causes false positives across the cluster; their Local Health Multiplier cut total false positives to 1.53% of baseline.

Finally, the directly comparable system already solved mid-generation failure. Borzunov et al. (NeurIPS 2023, arXiv:2312.08361) Algorithm 1 keeps client-side activations and replays `O(t)` of them to rebuild a failed stage. Measured on BLOOM-7.1B across 4 stages at 1024 tokens: at a 1e-2 failure rate, caching-with-restarts **did not finish within one hour** while Algorithm 1 achieved 2.17 steps/s.

### What we changed

- **[DECIDED]** Two-tier uptime-gated admission with Tier B standbys earning at the **full** availability rate, so the gate never punishes newcomers and "memory pledged == share earned" survives exactly.
- **[DECIDED]** Longest-uptime selection with `log₂` bucketing and randomised tie-breaking; a lint assertion that the planner may not read credit balance or pledge rank as a *selection* key.
- **[DECIDED]** φ-accrual detection with two thresholds and two cadences, plus Lifeguard's LHM, plus a Sanad-specific addition nobody else can make: **the polite node reports its own scheduler starvation.** Lifeguard has to *infer* that a node was descheduled; our node has ground truth. A uint32 of max-observed-scheduler-gap on every heartbeat, and the coordinator inflates that node's µ accordingly. Four bytes per message converts the polite node from the detector's worst enemy into its best input source.
- **[DECIDED]** A graceful-departure protocol with an advance-notice window, and the **graceful fraction g** tracked as a first-class SLO. At a 2-hour median residual and k=8 (raw success 0.735 over 400 s), reaching 0.95 requires g ≥ 0.83; at an 8-hour median residual, g ≥ 0.33 suffices. That number tells us whether the polite-node design is actually working.
- **[DECIDED]** Mid-run settlement, in a way that punishes nobody: the departing node is credited for layer-hours actually delivered; the client is charged for tokens delivered plus at most one replay; the difference is absorbed by the network rather than billed to either party. Invariant, as a test: credits minted for a run ≤ layer-hours verifiably delivered, for every possible failure point.
- **[DECIDED]** Departed nodes marked `RECOVERING` with a 3600 s soft reservation instead of `GONE`.
- **[DECIDED]** A churn harness driven by **Weibull, not exponential** (shape 0.5, scale = median/0.48), with a downtime mixture reproducing "returns within 1 hour p=0.55, within 1 day p=0.8, never p=0.15". Required scenarios: kill at token 1, at N/2, at the final token; kill the stage adjacent to the coordinator; kill two adjacent stages; and — the one that matters most and that a fixed TTL will fail — make a node **slow** (SIGSTOP for 5 s) rather than dead, and assert it is **not** evicted.

### What stays open

- **[OPEN]** The activation cache. This is the single highest-value engineering change in the whole review, and it is **not implementable on llama.cpp RPC** without leaving it or patching ggml, because llama-server owns the graph and our coordinator never sees per-stage activations. Interim: bound the work between restartable checkpoints at `D_max = −ln(1−ε)·median_residual/(k·ln 2)` — 26 s at a 2-hour residual, 105 s at 8 hours, for k=8 and ε=0.02 — and log `D_max` so the cost of not having the cache is a number rather than a vibe.
- **[OPEN]** Correlated failure. Bhagwan et al. (IPTPS 2003) found Overnet availabilities largely independent, but our early population will share a time zone, prayer and sleep schedules, ISPs and grid. Replicas that fail together are not replicas. The mitigation — require a replica to differ in /16 or ASN *and* in hour-of-week availability phase — is designed but not scheduled.

### What we get wrong, in this field's terms

There is no membership subsystem at all. "Trusted operators, no authentication" means we have no representation of a node being **suspected** — only up or gone. We have not implemented even a strongly-complete failure detector. Membership is not a feature to add after multi-machine works; it *is* what multi-machine means. And "withdrawal is never punished" is stated as policy with no mechanism: `stop_serving()` (`node.py:308–317`) posts `/leave` and immediately terminates the rpc-server, so every polite withdrawal currently costs a full generation restart. Unlike a Gnutella peer, our node *knows* it is about to leave and could say so. We are throwing away our own best asset.

---

## 5. Serving systems

### What the field says

**Our transport is not a pipeline; it is a synchronous serial relay routed through the coordinator, and this is provable from the source.** `ggml/src/ggml-rpc/ggml-rpc.cpp` advertises `props->caps = {async=false, …, events=false}` and leaves `cpy_tensor_async`, `event_record`, `event_wait`, `set_tensor_async`, `get_tensor_async` all NULL; `src/llama-context.cpp` enables llama.cpp's own micro-batch pipelining only when every non-CPU device supports async compute *and* events. So with `--rpc`, llama.cpp's pipeline parallelism is silently **off**, and by GPipe's bubble law (Huang et al., NeurIPS 2019, arXiv:1811.06965) utilization is `1/P`: at 4 nodes, **75% of every contributor's pledged hardware is idle at every instant.** Worse, two RPC endpoints have no direct copy path, so `ggml_backend_tensor_copy` falls back to a host-routed `malloc → get_tensor → set_tensor` and every inter-node boundary crosses the network **twice**, via the coordinator.

The consequences we have never seen, because we have only run on one machine over a loopback-speed interface:

- **Decode is latency-bound.** ~3K synchronous round trips per token. Network-only ceiling: 370 tok/s at 0.3 ms RTT, 111 at 1 ms, **3.70 at 30 ms**, 1.85 at 60 ms.
- **Prefill is bandwidth-bound.** Per-hop per-token transfer is `4e` bytes (prima.cpp, Li et al., ICLR 2026, arXiv:2504.08791) = 32 KiB at e=8192. A 4096-token prompt over 3 remote nodes moves **768 MiB on the wire** — 6.5 s at 1 Gbit/s, 65 s at 100 Mbit/s — before a single FLOP.
- These need opposite optimisations, which is the DistServe/Splitwise observation applied to a WAN chain.

Micro-batching would raise *prefill* throughput (utilization `M/(M+P−1)`: 25% → 73% at M=8, P=4) but does nothing for single-request decode, where only cross-request concurrency fills the pipe (`C/(C+P−1)`). **Speculative decoding is the only technique in the literature that improves single-request decode latency on a pipeline**, because it converts N sequential chain traversals into one batched verification traversal — and our regime is unusually favourable, because the draft runs on the coordinator so Leviathan's cost ratio `c` collapses to ~0.056 or ~0, moving the optimal draft length from llama.cpp's default of 3 to 8–12 and the speedup from ~1.9× to 3–4.7×.

The closest published system to Sanad beats llama.cpp by ~15× on exactly our workload: prima.cpp reaches **674 ms/token on a 70B across five consumer devices over Wi-Fi (320–610 Mbps, 3–7 ms latency), where llama.cpp takes 10,120 ms/token**. The mechanisms are a **ring** topology needing only sequential P2P transfers (no coordinator relay), pipelined-ring parallelism, and the Halda placement solver — which runs in 10–12 ms on 4–32 devices, cheap enough to re-solve on every join/leave. We got the partitioning right and the topology wrong. (Honest caveat: prima.cpp's headline gain comes substantially from disk/mmap prefetch overlap that our RAM-resident slices will not benefit from; the transferable win is the ring and the solver.)

And security is not a later milestone. **CVE-2026-34159** (GHSA-j8rj-fmpv-wcxw, CWE-119, patched in b8492) is unauthenticated arbitrary process memory read/write leading to RCE: `deserialize_tensor()` skips all bounds validation when a tensor's `buffer` field is 0, reachable via crafted `GRAPH_COMPUTE`, with ASLR defeated by pointer leaks from `ALLOC_BUFFER`/`BUFFER_GET_BASE`. CVE-2024-42479 (CVSS 9.8) is an earlier bug in the same path. Upstream says verbatim: *never run the RPC server on an open network.* The attack is symmetric.

### What we changed

- **[DECIDED]** Pin llama.cpp ≥ b8492 with a startup assertion; carry all RPC inside WireGuard or mTLS; never bind 50052 to a routable address; run rpc-server unprivileged. **The roadmap is re-sequenced to say authentication is a precondition for the multi-machine milestone, not adjacent to it.**
- **[DECIDED]** `--spec-type ngram-mod` with γ solved from measured `c` and α, re-solved every 200 tokens. Never the shipped default of 3.
- **[DECIDED]** `-np 8`, `-b 256`, `-ub 256` (below the 10 MiB `HASH_THRESHOLD`, whose crossover is 320 tokens at e=8192), `--cache-reuse`, `--slot-prompt-similarity`, `--no-context-shift`.
- **[DECIDED]** `rpc-server -c` — not optional. Without it every drain-and-rejoin re-uploads that node's entire weight slice; with it, tensors above 10 MiB take the `SET_TENSOR_HASH` path and are verified rather than re-sent. Publish `rejoin_bytes` and `rejoin_seconds`.
- **[DECIDED]** Engine-signature stability: quantize `pledge_mb` into 256 MB buckets and order the `--rpc` list by first-registration time. Today (`engine.py:265–268`) any 1 MB pledge fluctuation, or a node joining with a lexicographically small id, restarts the whole llama-server and re-streams the model to everyone.
- **[DECIDED]** Goodput-constrained capacity ladder with an explicit hop budget `K_max`, published on `/status`. At 30 ms RTT under a 200 ms TPOT, `K_max = 1`. That is the honest physical statement of what the network permits, and it is completely invisible in a memory-pledge model.
- **[DECIDED]** The seven serving numbers, gated per release: TTFT/TPOT percentiles; goodput; pipeline efficiency η compared against `M/(M+P−1)`; network fraction φ; bytes per output token; draft acceptance α; drain/rejoin cost.

### What stays open

- **[OPEN]** Whether to patch ggml-rpc or change transport. Three patches are identified and ordered by value per line: (a) `TCP_NODELAY` — the protocol sends a header then a payload over a strict request/response pattern, the canonical Nagle-plus-delayed-ACK pathology, potentially ~40 ms of injected delay per exchange across ~9 exchanges per token; three lines, measure before and after; (b) BF16 boundary activations, a free 2× on the wire (768 MiB → 384 MiB for a 4096-token prefill), lossless in practice since the values came from BF16/quantized weights; (c) peer-to-peer tensor handoff, taking transfers per ubatch from 2K to K+1 and round trips from 3K to 2K+1. Combining (b) and (c) takes that prefill to 256 MiB / 2.2 s at 1 Gbit/s — a 3× TTFT win at zero quality cost. **Given that we are pre-multi-machine, the architectural cost of evaluating prima.cpp or BloomBee (arXiv:2604.21072) as the pipeline layer will never be lower than it is right now.** We have not decided.
- **[OPEN]** A settlement gap we found while reading: `provable` (`coordinator.py:311`) requires the shard map to contain *only* `RPC0…RPCn`. If any transformer block lands on a non-RPC device — which happens when the pool is marginally too small — settlement is voided entirely: no credits minted, full refund, one warning event. That is a safe default, but it means a partially-CPU pipeline silently earns nothing. Reproduce it deliberately with a pool 10% under the tier requirement before deciding whether to credit the coordinator's own layers or refuse the tier.

### What we get wrong, in this field's terms

We call it a pipeline when it is a serial relay, and we optimise for capacity when the serving literature optimises for goodput. Those come apart badly here: at 30 ms RTT with three remote nodes the network floor is 270 ms per token, so a promoted 70B has goodput of exactly **zero** against any interactive SLO while the ladder reports a successful upgrade. We have built a system that can confidently climb into a state where it serves nobody. And thirty unit tests plus three recorded proofs establish that the system works; they establish nothing about TTFT, TPOT, per-hop RTT, bytes on the wire, bubble fraction, acceptance rate, marginal node value, or rejoin cost.

---

## 6. Volunteer motivation and trust

### What the field says

The attrition shape is known. Omoto & Snyder (*JPSP* 68(4):671–687, 1995; N=116 over 2.5 years): 90% expected to continue, 54% were active at 1 year, **16% at 2.5 years** — and satisfaction did *not* predict who stayed; self-focused motives (understanding, development, esteem) predicted longevity better than humanitarian ones. Online it is worse: across 7 Zooniverse projects (Sauermann & Franzoni, *PNAS* 112(3):679–684, 2015; 100,386 participants) only **17–40% ever return a second time** and ~10% do ~80% of the work.

Three things we are doing are, by the evidence, harmful or wasted.

**(1) The per-answer provenance footer.** Kizilcec (CHI 2016) randomized three transparency levels: no main effect; expectation violation lowered trust; and transparency *moderated* it. Low transparency, d=1.01 gap. **Medium** (procedural explanation only): gap fully closed, t(32)=0.06, p=0.95. **High** (procedure plus raw per-unit data): gap *reopened*, d=1.08, with lower self-rated comprehension than medium. Our node/layer/credit list is the high condition. It is also decorative provenance — citations raise trust even when **random**, and trust *drops* among people who actually check them (arXiv:2501.01303, AAAI 2025) — and it is Bernstein's full-observability regime, which cost 10–15% of performance in a randomized factory experiment (*ASQ* 57(2):181–216, 2012).

**(2) Credits as the hero metric.** Volunteer computing participants rate reputation **3.64/7** against collective motives **6.26/7** (Nov, Arazy & Anderson, *PLOS ONE* 9(4):e90375, 2014; N=3,178). We built the 3.64 channel and left the 6.26 channel empty. Performance-contingent tangible rewards carry d = −0.28 on free-choice intrinsic motivation (Deci, Koestner & Ryan, *Psych. Bulletin* 125(6):627–668, 1999); non-tradeability is a genuine and important mitigation, but whether it crowds in or out is decided by presentation (Frey & Jegen, *J. Econ. Surveys* 15(5):589–611, 2001).

**(3) No onboarding funnel and no self-benefit story** — in a population where self-focused motives predict survival.

On leaderboards the evidence is not a coin flip. The one field experiment isolating the mechanism (Kloc, Belo & Li, *Climbing the Ladder or Falling Behind*, working paper — **not peer-reviewed; we flag this because we are using it to justify not building something**) found ranking per se had ~zero engagement effect and that the benefit was mediated by **performance feedback**, not social comparison. Meanwhile the documented harms are concentrated on the majority: declining intrinsic motivation over 16 weeks (Hanus & Fox, *Computers & Education* 80:152–161, 2015); bottom performers *lowering* effort under relative scoring across 189,659 Topcoder submissions (Tsvetkova et al., CSCW 2022); and Old Weather volunteers naming the exact failure our constitution forbids — *"It was kind of a downer to come back and find after a few days that the number of transcriptions necessary to make captain had doubled"* (Eveleigh et al., CHI EA 2013).

### What we changed

- **[DECIDED]** Three provenance tiers, **medium by default**. Tier 0 always: one sentence, no identities. Tier 1 behind a disclosure: procedural only. Tier 2 (node handles): logged-in contributors, opt-in. Per-node credit amounts are removed from public chat entirely. Tier 1 auto-expands **only** on a measurable expectation violation.
- **[DECIDED]** Pseudonymous nodes by default; never IP, sub-country geolocation, hardware model, or uptime windows — uptime is an occupancy signal about someone's home. Full detail to the operator about their own node (Karau & Williams, *JPSP* 65(4):681–706, 1993, on identifiability); aggregate and procedural to strangers (Bernstein).
- **[DECIDED]** New dashboard hero metric: the **counterfactual**, computed by re-running `pick_tier` with this node's pledge removed — *"without your 16 layers, this group could only have run a 7B model; with them, it ran a 32B"* — plus beneficiary framing to *similar* others (Rashid et al., CHI 2006: 0.161 vs 0.086 for similar vs dissimilar; value-to-self at 0.069 is *worse* than no framing at all). Credits move to a small functional line labelled by what they do.
- **[DECIDED]** No persuasive re-engagement messaging. Ling et al. (*JCMC* 10(4), 2005) found value-explaining emails *depressed* contribution via reactance; Rashid et al. found the identical information embedded ambiently in the interface *raised* it by 3.7pp.
- **[DECIDED]** 200 starter credits presented as **200/1000 progress** (Nunes & Drèze, *JCR* 32(4):504–512, 2006: 34% vs 19% completion for an identical requirement).
- **[DECIDED]** Surface the polite node: *"Sanad stepped aside 14 times this week while you were working. Time you waited because of Sanad: 0.0 s."* An always-visible "Not now" with **no confirmation dialog and no guilt copy** — a guilt prompt flips Frey & Jegen's crowding-in condition to crowding-out.
- **[DECIDED]** Attribute the anonymous subsidy to the contributor: *"Your layers served 137 questions for people who have no node of their own"*, alongside realised benefit. With ~50% conditional cooperators (Fischbacher, Gächter & Fehr, *Economics Letters* 71(3):397–404, 2001), legible reciprocity is what keeps the reserve sustainable without touching the reserve.
- **[DECIDED]** Four lines added to the constitution: no public ranking of operators; no displayed metric may decrease because an operator was away; public per-answer provenance is procedural, never personal; no persuasive re-engagement messaging. Each is currently an implicit choice that would be silently reversed the first time someone ships a "fun" feature.
- **[REJECTED]** A leaderboard. Also streaks and consecutive-day counters — GitHub's silent removal of streak counters changed weekend and single-contribution behaviour measurably (Moldon, Strohmaier & Wachs, ICSE 2021), and a streak is mechanically a penalty for absence.

### What stays open

- **[OPEN]** A guest / one-evening mode for dabblers — bounded sessions, no account, a claimable credit token. Designed, unscheduled.
- **[OPEN]** Peer-granted thanks, rate-limited to one per operator per week with a required reason. Restivo & van de Rijt (*PLOS ONE* 7(3):e34358, 2012) measured +60% median productivity and a significant retention effect from a single barnstar — but their 2014 replication found the effect **only** for the most productive contributors. Recognition sustains a core; it does not recruit or revive the marginal. We should not build a programme around it.

### What we get wrong, in this field's terms

Our escrow-and-settle design is a latent withdrawal penalty. If a run fails because a node drained politely — the behaviour we explicitly want — and settlement docks credit, we have written "withdrawal is never punished" into the constitution and "withdrawal is punished" into the ledger. Multi-machine will create that situation constantly. And nothing in the product builds competence: an operator today cannot tell whether their node is helping or dragging the pipeline, and quality anxiety resolves as exit.

---

## 7. Where the sciences disagreed, and what we chose

The full list is in the review record; four are worth stating here because the resolution changed the design.

**Lottery versus deterministic ranking.** Mechanism design wants a proportional-share lottery because strict ranking invites the marginal-bid and Sybil-slot attacks that BitTyrant monetised. Queueing rejects lotteries because their O(√n) error is weaker than what our constitution promises. **We chose a deterministic APQ with a *bounded* rate ratio.** Bounding `b ∈ [1/3, 1]` caps the payoff from outbidding the marginal job at 3×, and only at saturation — which removes the reason to mount the attack without paying the lottery's variance. Neither reviewer proposed this.

**Decay versus cap.** Commons wanted a 30-day demurrage half-life; HCI requires that no displayed metric decrease because an operator was away. **We chose the cap and rejected decay.** A cap makes hoarding impossible and prevents a tenure aristocracy — every active account saturates — while never reducing an absent operator's balance. We also separated three quantities that were being conflated: lifetime contribution (monotone, displayed), spendable balance (capped), and priority weight (a rate, bounded to 3×).

**Publish everything versus publish almost nothing.** DP4B demands verifiability by appropriators; Kizilcec and Bernstein demand restraint at the user-facing surface. **Cryptographic pseudonymity satisfies both.** Aggregate and procedural to everyone, keyed on Ed25519 fingerprints; per-node detail only to that node's operator.

**Constant supply versus always serving guests.** Under a closed loop, guests burn nothing, and guests are most of the demand. Rather than add a commons account funded by a tithe, we let guest-served work carry weight identically to member-served work, so **guest service dilutes the payout rather than inflating the supply.** A network serving only guests distributes a pool of zero — which is correct, because when nobody is competing for priority, priority is worth nothing. When congestion arrives, members burn, the pool fills, and operators are paid for the memory they were holding all along.

---

## 8. How to falsify this document

Every claim above is meant to be checkable, and several are currently unchecked. In rough order of what would embarrass us most:

1. Run the **long-generation test**: a credit-holder issuing 8k-token requests against anonymous 100-token ones. Assert anonymous *token* share ≥ 1/3. This fails today by construction; if it passes, our reading of the reserve is wrong.
2. Run the **slow-not-dead test**: `SIGSTOP` a node for 5 s mid-generation and assert it is not evicted. A fixed 15 s TTL against a 3 s heartbeat should survive this; a busier machine will not.
3. Measure **η = Σbusy_i/(P·wall)** on a real two-machine run. If it lands near `1/P`, the serial-relay diagnosis is confirmed and three quarters of every contributor's pledge is idle.
4. Measure a **4096-token prefill over a real 100 Mbit/s link** and compare against the predicted 65 s.
5. Replay a trace and check **Σρ_k·W_k** against Kleinrock's invariant. A persistent shortfall means the scheduler idles while work is queued.
6. Assert **Σbalances + pool == M** after a randomised submit/settle/refund sequence.
7. Deliberately under-provision the pool by 10% and check whether `provable` goes false and settlement is voided.

---

## 9. Sources

Commons: Hardin (*Science* 162:1243–1248, 1968); Ostrom (*Governing the Commons*, CUP 1990; *JEP* 14(3):137–158, 2000; *PNAS* 104(39):15181–15187, 2007); Ostrom, Walker & Gardner (*APSR* 86(2):404–417, 1992); Crawford & Ostrom (*APSR* 89(3):582–600, 1995); Hess & Ostrom (*Law & Contemp. Problems* 66(1–2):111–145, 2003); Cox, Arnold & Villamayor-Tomás (*Ecology & Society* 15(4):38, 2010); Baggio et al. (*Int. J. Commons* 10(2):417–439, 2016); Cason & Gangadharan (*J. Theor. Politics* 28(1):44–73, 2016); DeCaro et al. (*PLoS ONE* 19(8):e0307832, 2024); Gürerk, Irlenbusch & Rockenbach (*Science* 312(5770):108–111, 2006); Adar & Huberman (*First Monday* 5(10), 2000); Douceur (IPTPS 2002); Feldman et al. (*IEEE JSAC* 24(5):1010–1019, 2006); Schweik & English (*Internet Success*, MIT Press 2012); Forte, Larco & Bruckman (*JMIS* 26(1):49–72, 2009); Halfaker et al. (*Am. Behav. Sci.* 57(5):664–688, 2013); Kharazian, Starbird & Hill (CSCW 2024, doi:10.1145/3637338); O'Mahony & Ferraro (*AMJ* 50(5):1079–1106, 2007); Frischmann, Madison & Strandburg (*Governing Knowledge Commons*, OUP 2014); Garrido-Merchán (arXiv:2606.15466, 2026).

Mechanism design: Kash, Friedman & Halpern (EC 2007); Friedman, Halpern & Kash (EC 2006); Hartline & Roughgarden (STOC 2008); Condorelli (*GEB* 75(2):613–624, 2012); Gorokh, Banerjee & Iyer (EC 2017; *Math. OR* 46(4), 2021); Prendergast (*JPE* 130(8), 2022); Sweeney & Sweeney (*JMCB* 9(1):86–89, 1977); Levin et al. (SIGCOMM 2008); Piatek et al. (NSDI 2007); Cheng & Friedman (P2PECON 2005); Ashlagi, Kerimov, Tamuz & Zhao (*Management Science* 2026; arXiv:2405.12414); Naor (*Econometrica* 37(1):15–24, 1969); Barlow & Proschan (1965).

Queueing: Cobham (*JORSA* 2(1):70–76, 1954); Kleinrock (*NRLQ* 11(3):329–341, 1964; 12(2):181–192, 1965); Bolch (*Acta Informatica* 10:105–109, 1978); Demers, Keshav & Shenker (SIGCOMM 1989); Shreedhar & Varghese (SIGCOMM 1995; *ToN* 4(3):375–385, 1996); Parekh & Gallager (*ToN* 1(3):344–357, 1993); Waldspurger & Weihl (OSDI 1994; MIT/LCS/TM-528, 1995); Kelly (*ETT* 8(1):33–37, 1997); Mo & Walrand (*ToN* 8(5):556–567, 2000); Bertsimas, Farias & Trichakis (*Oper. Res.* 59(1):17–31, 2011); Fuhrmann & Cooper (*Oper. Res.* 33(5):1117–1129, 1985); Wierman & Harchol-Balter (SIGMETRICS 2003); Bender, Chakrabarti & Muthukrishnan (SODA 1998); Jain, Chiu & Hawe (DEC-TR-301, 1984); Stanford, Taylor & Ziedins (*Queueing Systems* 77(3):297–330, 2014); Sheng et al. (OSDI 2024, arXiv:2401.00588).

Churn: Stutzbach & Rejaie (IMC 2006); Godfrey, Shenker & Stoica (SIGCOMM 2006); Leonard, Rai & Loguinov (SIGMETRICS 2005); Mickens & Noble (NSDI 2006); Chandra & Toueg (*JACM* 43(2):225–267, 1996); Hayashibara et al. (SRDS 2004); Das, Gupta & Motivala (DSN 2002); Dadgar, Phillips & Currey (arXiv:1707.00788); Bhagwan, Savage & Voelker (IPTPS 2003); Gummadi et al. (SOSP 2003); Borzunov et al. (NeurIPS 2023, arXiv:2312.08361).

Serving: Huang et al. (NeurIPS 2019, arXiv:1811.06965); Yu et al. (OSDI 2022); Kwon et al. (SOSP 2023); Leviathan, Kalman & Matias (ICML 2023, arXiv:2211.17192); Agrawal et al. (OSDI 2024, arXiv:2403.02310); Zhong et al. (OSDI 2024, arXiv:2401.09670); Patel et al. (ISCA 2024, arXiv:2311.18677); Butler et al. (SC24, arXiv:2407.11798); Mei et al. (ASPLOS 2025, arXiv:2406.01566); Li et al. (ICLR 2026, arXiv:2504.08791); arXiv:2604.21072 (BloomBee — venue unconfirmed); arXiv:2411.09510; CVE-2026-34159 / GHSA-j8rj-fmpv-wcxw; CVE-2024-42479.

HCI: Omoto & Snyder (*JPSP* 68(4):671–687, 1995); Karau & Williams (*JPSP* 65(4):681–706, 1993); Deci, Koestner & Ryan (*Psych. Bull.* 125(6):627–668, 1999); Frey & Jegen (*J. Econ. Surveys* 15(5):589–611, 2001); Fischbacher, Gächter & Fehr (*Econ. Letters* 71(3):397–404, 2001); Ling et al. (*JCMC* 10(4), 2005); Nunes & Drèze (*JCR* 32(4):504–512, 2006); Rashid et al. (CHI 2006); Chen, Harper, Konstan & Li (*AER* 100(4):1358–1398, 2010); Restivo & van de Rijt (*PLOS ONE* 7(3):e34358, 2012; *ICS* 17(4):451–467, 2014); Bernstein (*ASQ* 57(2):181–216, 2012); Eveleigh et al. (CHI EA 2013; CHI 2014); Nov, Arazy & Anderson (*PLOS ONE* 9(4):e90375, 2014); Sauermann & Franzoni (*PNAS* 112(3):679–684, 2015); Hanus & Fox (*Computers & Education* 80:152–161, 2015); Kizilcec (CHI 2016); Bansal et al. (CHI 2021); Moldon, Strohmaier & Wachs (ICSE 2021, arXiv:2006.02371); Tsvetkova et al. (CSCW 2022); arXiv:2501.01303 (AAAI 2025); Kloc, Belo & Li (working paper — not peer-reviewed).

---

*Corrections are welcome as issues. If a number here is wrong, that is a bug in this document and should be filed as one.*