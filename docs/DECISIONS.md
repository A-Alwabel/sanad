# Decision log

Why things are the way they are — including the things we got wrong and changed.

Every entry records the decision, what forced it, and (where it replaced an
earlier choice) why the earlier one was wrong. Reversals are kept, not tidied
away: a project that hides its corrections teaches nobody, and this one is
supposed to be honest by construction. Commit messages carry the same reasoning
in more detail; this file is the readable index of it.

Format: **what changed** · *why* · what it replaced.

---

## Foundations

**Sanad exists as a join, not an invention** · Petals proved sharded volunteer
inference works and died for lack of an incentive layer; AI Horde proved
non-tradeable credits sustain a volunteer network for years but requires every
worker to hold a whole model. Neither half alone is the product. · No prior
choice; this is the founding thesis ([CONCEPT.md](CONCEPT.md)).

**Credits are non-tradeable, permanently** · Every reward-for-participation
scheme in this space that could be sold, was farmed. AI Horde's kudos survived
years precisely because they buy priority and nothing else. Made
constitutional so it cannot be quietly reversed under monetisation pressure —
Sahara's points-to-token migration shows that pressure is real. ·
[GOVERNANCE.md](../GOVERNANCE.md).

**Positioned as public infrastructure, not a cheaper API** · Selling a swarm as
"free inference" is part of what killed Petals: centralized free tiers beat it
on speed and cost, and always will. The defensible axis is durability and
ownership. · Replaced the obvious framing, deliberately.

---

## Engineering reversals

**Per-request engine → resident engine (v0.3)** · Spawning `llama-completion`
per request re-streamed the model to every node before a single token appeared:
~5 s of dead air per question. A resident `llama-server` holds the pipeline and
answers immediately (measured 5.17 s → 0.07 s to first token). · Replaced the
stateless-but-wasteful design that made v0.1–v0.2 easy to reason about; the
ladder still gets cheap tier switches because the engine rebuilds only when the
pipeline actually changes.

**Raw `/completion` → `/v1/chat/completions` with the model's own template (v0.4)**
· Sending a bare prompt meant an instruct model never saw its chat template and
had no history, so it rambled and answered every message as if it were the
first. · Replaced the simplest thing that produced text with the thing that
produces *answers*.

**PowerShell CPU sampling → native kernel calls (v0.2.1)** · The busy sensor
shelled out to PowerShell; under exactly the CPU saturation it was meant to
detect, PowerShell itself starved and the sensor went blind. Now `kernel32` on
Windows, `/proc` on Linux, `getloadavg` elsewhere — no subprocess. · Replaced a
sensor that worked in testing and would have failed in the field.

**In-memory ledger → append-only file (v0.4)** · Restarting the coordinator
erased everyone's contribution, contradicting the governance promise that
withdrawal is never punished. Entries are now fsynced per write and replayed at
startup. · Replaced the assumption that a coordinator restart is rare enough
not to matter. It is not, and "rare" is not a fairness argument.

**Ledger: apply-then-write → write-then-apply (v0.4.1)** · The old order could
leave a balance in memory that the file did not back, silently inventing credit
across a crash. · Reversal of my own ordering, found by adversarial review.

**Static credit-order queue → accumulating priority (v0.4.1)** · Ordering by a
credit *stock* is Cobham (1954) static priority, whose lowest class waits
without bound. Priority now accrues with waiting time at a credit-derived rate,
so a guest's score climbs until it wins. · Replaced a scheme whose fairness
claim ("anonymous users are always served") the code could not actually
guarantee under sustained load.

**Global FIFO reserve → per-account reserve lane (v0.4.1)** · Giving every third
slot to the globally-oldest job let one guest flooding requests own the entire
reserve and starve every other guest — the exact population the lane exists to
protect. The lane now rotates over accounts. · Found by applying Ostrom's
commons analysis: an unbounded shared sub-pool is open access, and open access
is what the tragedy of the commons actually describes.

**Chain repair: "no heartbeat since this job started" → "silent past the TTL"
(v0.4.1)** · Under concurrency, one failed request evicted every healthy node,
turning a single failure into a network-wide outage. A node heartbeating
normally is no longer convicted by someone else's failure. · Reversal of a rule
that was correct when only one job could run at a time.

**Engine rebuild: stop immediately → drain first (v0.4.1)** · With concurrency,
rebuilding the pipeline terminated `llama-server` while other people were
mid-answer; worse, the resulting `ConnectionResetError` is not a `URLError`, so
it bypassed chain repair entirely and surfaced as a raw socket error. Engines
now retire: stop accepting work, wait for in-flight answers, then stop. ·
Reversal of a lifecycle that was safe only because v0.3 served one request at a
time.

---

## Claims we corrected

**"3.0x more throughput from concurrency" → "responsiveness, not throughput"**
· The figure compared concurrent wall time against the *sum of contended*
durations, which flatters the result. Measured against a real serial run,
concurrency on CPU inference is roughly a wash (0.93x–1.78x across runs) because
the slots share the same cores. What it genuinely buys is head-of-line
blocking: a short question is answered in ~0.5 s while a 320-token answer is
still streaming. · The proof now measures a true serial baseline and asserts the
property that is real.

**"Discovery is broadcast-only, it never leaves the subnet" → accurate wording**
· Nodes *ask* by broadcast, but the responder was a UDP socket bound to all
interfaces that would answer any unicast probe. It now honours `--bind`,
refuses non-private sources, rate-limits, and the docs say what it actually is.
· A published claim that was simply false.

**"Anonymous users are always served" → "served within bounded time"** ·
Liveness was never proven; the original three-job test showed ordering only.
The claim now matches a mechanism that provides it. · See the two queue
reversals above.

**`audit()` "verifies the ledger" → re-reads the file, or admits it cannot** ·
The first version compared two in-memory structures updated in the same
critical section, which proves nothing. It now re-reads the file from disk, and
an in-memory ledger reports `durable: false` instead of claiming a verification
it did not perform. · Replaced a check that was reassuring rather than true.

**"Three milestones" while listing four; "7 tests" when there were 12** ·
Small, but this project's whole claim is that its numbers can be trusted. ·
Corrected as found.

**"Still one machine" → two isolated networks (v0.5)** · Every proof through
v0.4 ran on one host, which left the project's central claim untested. Nodes
now run in separate Linux containers on separate Docker networks that cannot
reach each other; only the coordinator bridges them. · Replaced an emulation
gap we had been honest about but had not closed. The remaining gap — two real
ISPs — has a written free path in [net/deploy/README.md](../net/deploy/README.md).

**Measured the WAN cost instead of predicting it** · We had been citing a
published figure (~9 tok/s for a 7B at 80 ms RTT) as our expectation. With
`tc netem` on the container links we now have our own number: 31.3 → 4.2 tok/s,
a 7.5x slowdown at 40 ms per link. · Replaced a borrowed estimate with a
measurement of our own system, which also quantifies the design's central
tension: the models that most need sharding are the ones that shard worst.

---

## Deliberate non-decisions

**No blockchain, no token, no consensus layer** · Proof-of-useful-work cannot
secure a chain (Gridcoin conceded this in 2014), and a tradeable credit makes
farming profitable rather than merely annoying. Control decentralisation is
intended to come from *federation* — several coordinators recognising each
other — not from consensus. · Stated so nobody has to guess whether it is
coming.

**Training is deferred** · [RFC 0001](rfc/0001-human-loop.md) collects
preference data and ships its defenses first, because every precedent that
rewarded ratings was gamed within days and 0.5% poisoned pairs suffice to embed
a backdoor. Promising a trained model before the data earns it is how
credibility is spent.

**Frontier-scale pretraining is not attempted** · The best permissionless
pretraining run to date matches 2023-era quality, and Prime Intellect retreated
to a centralized cluster for its own flagship. Competing there means losing
slowly with other people's money.

---

## How to add an entry

When you change something that a reasonable person might later ask "why is it
like this?" — or, more importantly, "why did it used to be different?" — add a
line here in the same format. Reversals are the most valuable entries in the
file, so write those first and write them plainly.
