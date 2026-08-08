# Announcement kit

Copy-paste drafts for introducing Sanad. Written to match the project's voice:
plain, specific, limits stated up front. Post from your own accounts — nothing
here should be automated, and nothing should be posted anywhere the community
would consider it spam.

**One rule for all of these:** never claim more than [PROOF.md](PROOF.md) shows.
If a reply asks whether it runs across the internet yet, the answer is "not yet —
that's the next milestone," every time.

---

## 1. Hacker News (Show HN)

**Title** (80 char limit — this is 74):

```
Show HN: Sanad – chain your devices to run open LLMs too big for any one
```

**Body:**

```
The best open-weight models are free to download and impossible to run: 400GB-1TB
of weights that no personal device holds. So "open" models are, in practice, open
to whoever owns a datacenter — or to whoever a vendor's free tier happens to
still cover this month.

Sanad chains ordinary devices into one: each node holds a slice of a model's
layers, and the chain serves models none of them could run alone. Contributing
compute earns non-tradeable credits that buy queue priority — never money, never
a token, never access (anonymous users are always served; every third queue slot
is strictly first-come-first-served so nobody starves).

The idea is a join of two proven halves that nobody had run together: Petals
proved layer sharding across volunteer devices in 2022 and died in ~2024 with no
incentive layer; AI Horde's kudos economy proved non-tradeable credits sustain a
volunteer network for years, but every Horde worker must host a whole model, so
the big ones are out of reach.

What actually runs today (all reproducible, transcripts in the repo):
- A real model's layers split across two node processes over TCP — neither ever
  held the whole model — generating real text at ~44 tok/s (CPU, loopback).
- A capacity ladder: a second node joined and the network auto-upgraded the model
  it serves; the node left and it stepped back down without stopping service.
- A "polite node": when its owner started heavy work, it drained out by itself,
  returned all memory, and rejoined by itself when the machine went quiet. Your
  device, your priority. Leaving is never punished — credits are kept.
- Credits weighted by layer share: memory lent equals share earned.

Honest scope, because over-promising is how this field loses trust: this ran on
ONE machine (separate processes over loopback), with trusted nodes and small
models. Two machines on two networks is the next milestone. Activation privacy
is unsolved here as it is everywhere — nodes can reconstruct prompts from what
passes through them. Verification is detect-and-eject, not cryptographic proof.
Single-stream speed over real WAN links will be much worse than the loopback
number above.

It's day one and I'm looking for people to argue the design into shape more than
users. The next chapter (RFC 0001: rating answers to build the first fully open
human-preference dataset) is up for review with eight genuinely open questions.

https://github.com/A-Alwabel/sanad
```

**If it gets traction, expect and prepare for:**
- *"This is just Petals."* → Yes, for the sharding half. The delta is the credit
  economy Petals never had, which is the specific thing its post-mortems blame.
- *"Why not crypto?"* → Non-tradeability is constitutional (GOVERNANCE.md). Every
  reward-for-participation system in this space got farmed; a tradeable credit
  makes farming profitable instead of merely annoying.
- *"~44 tok/s is loopback, that's meaningless."* → Agreed, and it's labeled as
  such in PROOF.md. Published WAN measurements put a 7B at ~9 tok/s at 80ms RTT.

---

## 2. r/LocalLLaMA

**Title:**

```
I chained two machines' processes to serve one model's layers, with a
non-tradeable credit system so contributors get queue priority (open source)
```

**Body:**

```
Petals proved you can split a big model across volunteer devices. It's been dead
since ~2024, and the post-mortems agree on why: no incentive to keep hosting.
AI Horde proved the opposite half — non-tradeable "kudos" keep a volunteer
network alive for years — but every Horde worker hosts a whole model, so 70B+
never really shows up there.

I built the join. Sanad shards a model's layers across nodes (llama.cpp's RPC
backend does the inference; my layer is the network + fairness on top) and pays
serving nodes non-tradeable credits that buy queue priority and nothing else.

Three things it does that I haven't seen combined anywhere:

1. Capacity ladder — the coordinator serves the largest model the pooled pledged
   memory can hold. Second node joins → model auto-upgrades. Node leaves → steps
   down, keeps serving. The network's brain grows with its community.

2. Polite node — you pledge how much RAM you lend; the engine runs at
   BELOW_NORMAL priority so your games/work always win the CPU; a sensor watches
   *other-process* load and drains the node out gracefully when you need the
   machine, then rejoins when it's quiet. Withdrawal is never punished.

3. Weighted credits — your share of each token's credit equals your share of the
   model's layers. Memory lent = share earned.

Honest limits, because this sub deserves them: this ran on one machine (separate
processes, loopback TCP), trusted nodes, 0.5B/1.5B models chosen so the proof is
cheap for anyone to reproduce. Two machines on two networks is next. Nodes see
your activations, so it's not private. llama.cpp's RPC backend is upstream-labeled
"fragile and insecure" — trusted machines only for now.

Everything is reproducible: `python proof/run_first_light.py` starts the whole
network, proves the layer split, runs inference, and asserts the credit rules.
Transcripts of the actual runs are committed in the repo.

Apache-2.0. Looking for reviewers more than users — especially anyone from the
Petals/Hivemind, AI Horde, Parallax, or BloomBee worlds.

https://github.com/A-Alwabel/sanad
```

---

## 3. X / Twitter thread

```
1/ The best open AI models are free to download and impossible to run.
400GB-1TB of weights. No personal device holds that.

So "open" models are only open to whoever owns a datacenter.

I've been building the other answer: chain our devices, and serve the model
together. 🧵

2/ It's called Sanad — Arabic for the chain of transmission that carries
knowledge from person to person, each link vouching for the next.

Each device holds a slice of the model's layers. The chain serves what none of
them could alone.

3/ Two halves of this were already proven, and nobody had run both:

Petals (2022): sharding across volunteer devices — worked, then died with no
incentive layer.

AI Horde: non-tradeable credits that keep volunteers around for years — but
every worker must host a WHOLE model.

Sanad is the join.

4/ What runs today, with committed transcripts:

A real model split across two node processes over TCP — neither ever held the
whole model. Real text out the far end. Serving minted credits, split by the
layers each node actually carried.

5/ The network breathes:

A second node joined → it auto-upgraded to a bigger model.
Its owner launched a heavy task → the node drained out by itself, returning all
memory.
Machine went quiet → it rejoined by itself.

Your device, your priority. Always.

6/ Credits buy queue priority. Not money. Not a token. Not access — anonymous
users are always served, and every third queue slot ignores credits entirely so
nobody can be starved out.

Non-tradeable, forever. It's a constitutional commitment, not a phase.

7/ Honest limits, because over-promising is how this field loses trust:

One machine so far. Trusted nodes. Small models. Nodes can read your prompts
from the activations that pass through them — unsolved everywhere, not just here.

Two machines on two networks is the next milestone.

8/ It's day one and I want arguments more than users.

RFC 0001 is open: rate answers, earn credits, build the first fully open human
preference dataset. Eight genuinely unanswered questions in it.

Apache-2.0 🌍
https://github.com/A-Alwabel/sanad
```

---

## 4. Message to Haidra / AI Horde (the most important one)

Post in their community space, not as a DM blast. The ask is real: they carry
three years of scar tissue from precisely the fight RFC 0001 walks into.

```
Hi — I've built something that leans heavily on the Horde's design, and I'd
rather show it to you early than surprise you with it later.

Sanad is a community inference network that splits one model's layers across
several nodes (Petals-style), so a chain of ordinary machines can serve models
none of them could hold alone. The incentive layer is yours, adopted almost
verbatim in spirit: contribute compute, earn credits, spend them on queue
priority — never buyable, never sellable, anonymous users always served. Your
kudos economy is the only design in this space I could find that has survived
years without turning into speculation, and the docs say so by name.

Two reasons I'm writing:

1. RFC 0001 (in review now) proposes letting users rate answers to build an open
   preference dataset, with small credits for validated ratings. Then I read
   db0's posts about the 2023 LAION ratings collaboration — bots showing up
   "almost immediately" even though kudos were worthless and the service was free
   without them, the captcha, the volume collapse that revealed how much of it had
   been fraud, the per-rating trust metadata as the lasting answer. That history
   changed the design: defenses ship before the first credit is paid, credits vest
   only after validation, and rating issuance is capped far below serving. If
   there's more you'd share about what did and didn't work — especially whether a
   text-rating loop was ever tried — I'd rather learn it from you than rediscover
   it the expensive way.

2. If shared policy language on non-tradeable credits would be useful to either
   of us, I'd like that. We're defending the same line from the same pressure.

Repo (Apache-2.0): https://github.com/A-Alwabel/sanad
RFC: docs/rfc/0001-human-loop.md

No ask beyond your critique. If parts of this are wrong-headed, that's the most
useful thing you could tell me.
```

---

## 5. One-line description (for repo About, link posts, bios)

```
Run AI models too big for any one device — together. A community inference
network: sharded layers + non-tradeable credits. Public infrastructure, not a
product.
```
