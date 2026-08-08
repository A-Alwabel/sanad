# Proofs that Sanad works

Five milestones, all fully reproducible:

| Milestone | What it proves | Transcript | Reproduce |
|---|---|---|---|
| **Part 1 — First Light** (v0.1) | Real sharded inference + live credit economy | [first-light transcript](../net/proof/artifacts/first-light-2026-08-05.txt) | [run_first_light.py](../net/proof/run_first_light.py) |
| **Part 2 — The Living Network** (v0.2) | Capacity ladder + polite node + weighted fairness | [living-network transcript](../net/proof/artifacts/living-network-2026-08-05.txt) | [run_living_network.py](../net/proof/run_living_network.py) |
| **Part 3 — It Works** (v0.3) | Resident pipeline, live streaming, chat UI, real LAN | [it-works transcript](../net/proof/artifacts/it-works-2026-08-08.txt) | [run_it_works.py](../net/proof/run_it_works.py) |
| **Part 4 — Usable Together** (v0.4) | One-command join, conversation memory, durable credits, concurrency | [v0.4 transcript](../net/proof/artifacts/v04-2026-08-08.txt) | [run_v04.py](../net/proof/run_v04.py) |
| **Part 5 — Two Networks** (v0.5) | Sharding across genuinely isolated networks; WAN cost measured | [two-networks transcript](../net/proof/artifacts/two-networks-2026-08-08.txt) | [run_two_networks.py](../net/proof/run_two_networks.py) |

---

# Part 1 — First Light

**Date:** 2026-08-05 · **Status:** PASS · **Raw transcript:** [net/proof/artifacts/first-light-2026-08-05.txt](../net/proof/artifacts/first-light-2026-08-05.txt) · **Reproduce:** [net/proof/run_first_light.py](../net/proof/run_first_light.py)

On 2026-08-05, a real Sanad network served real LLM inference for the first
time: a model's layers physically split across two separate node processes over
TCP, requests flowing through the chain, and the credit ledger doing exactly
what [GOVERNANCE.md](../GOVERNANCE.md) promises — contribution buys priority,
and nobody is ever locked out.

> **A note on the transcript.** The First Light transcript was captured on the
> v0.1 code, which split credits evenly across nodes and used the pre-ladder
> response schema. Re-running the script on the current (v0.2+) code still
> passes, but the output differs slightly: credit amounts are weighted by each
> node's layer share rather than split evenly, and responses carry a `model`
> field.

## Environment

| Component | Version / detail |
|---|---|
| Machine | Intel i9-10900K (20 threads), 32 GB RAM, Windows 11 Pro |
| Python | 3.12.4 (sanad_net is stdlib-only) |
| Inference engine | llama.cpp **b10276** (6ea215d17), official Windows CPU build, RPC backend |
| Model | Qwen2.5-0.5B-Instruct, GGUF Q4_K_M (24 transformer layers + output) |
| Model SHA-256 | `74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db` |
| Topology | 1 coordinator + 2 nodes (`riyadh-a` / operator amina, `jeddah-b` / operator bilal), one physical machine, TCP on loopback |

## What was proven

### 1. The model really is split across nodes

llama.cpp's verbose load log, parsed by the coordinator into the shard map
returned with every answer (from the transcript):

```json
"shard_map": {
  "RPC0": { "endpoint": "127.0.0.1:50071", "layers": "0-12",  "n_layers": 13 },
  "RPC1": { "endpoint": "127.0.0.1:50070", "layers": "13-24", "n_layers": 12 }
}
```

Layers 0–12 lived in one node's process, layers 13–24 in the other's; during
generation, activations crossed TCP between them for every token. Neither node
process ever held the whole model in memory — though, to be fully honest, the
coordinator's machine stores the full GGUF and streams each node's shard to it
over TCP on every request; distributing model *storage* is future work.

### 2. Real text came out the other end

Prompt (anonymous user): `A mining pool is` →

> "a network of servers that work together to perform complex calculations and
> data processing tasks. They are often used in the mining industry to increase
> the speed and efficiency of mining"

31 decode tokens at **43.7 tok/s** (captured run; CPU-only, two-node chain,
loopback TCP; this number exists to be honest, not to impress — WAN latency
will cut it hard, see [ARCHITECTURE.md](ARCHITECTURE.md)).

### 3. The credit economy works end to end

- Serving that first job minted **15.5 credits to each operator** (31 tokens
  split evenly across 2 nodes) — earned by serving, exactly as designed.
- Then three users raced: a filler job was running, anonymous **zed** was
  queued *first*, contributor **amina** (15.5 credits) was queued *after* him.
  Completion order from the transcript:

```
completion order (user, priority_at_submit):
  anon     priority=0.0      <- was already running
  amina    priority=15.5     <- queued after zed, served before him
  zed      priority=0.0      <- anonymous: served last, but SERVED
```

- Spending is real too: amina's balance dropped when she consumed
  (final state: bilal 50.0, amina 27.0 — she paid tokens for her own job).
  Credits buy **priority, never access**: zed paid nothing and still got his
  answer.

## What this does NOT prove (yet)

- **Not multi-machine:** all processes ran on one physical machine over
  loopback TCP. The protocol is network-transparent, but the two-houses,
  two-cities run is the next milestone.
- **Not open membership:** llama.cpp's RPC backend is upstream-labeled
  "fragile and insecure"; nodes must be trusted machines. This matches the
  permissioned-first trust model in [ARCHITECTURE.md](ARCHITECTURE.md).
- **Not private:** activations are readable by the nodes they pass through —
  the field-wide unsolved problem (arXiv 2503.09291) documented in
  [ARCHITECTURE.md](ARCHITECTURE.md).
- **Not distributed storage:** the coordinator's machine holds the full GGUF
  file and streams each node's shard to it over TCP on every request. Nodes
  lend memory and compute, not storage; distributing model storage is future
  work.
- **Not efficient:** the model reloads per request (~5–6 s overhead, derived
  from the captured wall times); a resident pipeline is next.
- **Not big:** 0.5B parameters. Chosen so the proof is cheap to reproduce
  anywhere. The identical mechanism loads 70B-class models across enough nodes
  (llama.cpp splits by each node's reported free memory).

## Reproduce it yourself

```powershell
cd net
python -m unittest discover -s tests -v        # unit tests, no binaries needed
python proof/run_first_light.py --llama-bin ../.local/bin `
    --model ../.local/models/qwen2.5-0.5b-instruct-q4_k_m.gguf
```

Setup for the binaries and model: [net/README.md](../net/README.md). The
script asserts, specifically: the model is sharded across at least two
devices; decode tokens were produced; both operators' balances are positive;
the contributor queued last overtook at least one anonymous rival (credits buy
priority); and every anonymous user was still served (under the v0.2.1 hybrid
scheduler, every third slot is strictly first-come-first-served). Then it
exits with `FIRST LIGHT: PASS`.

---

# Part 2 — The Living Network (v0.2)

**Date:** 2026-08-05 · **Status:** PASS · **Raw transcript:** [net/proof/artifacts/living-network-2026-08-05.txt](../net/proof/artifacts/living-network-2026-08-05.txt) · **Reproduce:** [net/proof/run_living_network.py](../net/proof/run_living_network.py)

Same environment as Part 1, plus a second catalog tier
(Qwen2.5-**1.5B**-Instruct Q4_K_M, 29 layers). Three claims, all asserted by
the script and visible in the transcript:

## A. The capacity ladder — the network grows as its community grows

The coordinator always serves the **largest catalog model the pledged memory
pool can hold** (file size × 1.4 safety factor). From the event log, verbatim:

```
LADDER UP:   model -> qwen2.5-0.5b... (pool 1000 MB across 1 nodes)
LADDER UP:   model -> qwen2.5-1.5b... (pool 1700 MB across 2 nodes)   <- node B joined
LADDER DOWN: model -> qwen2.5-0.5b... (pool 1000 MB across 1 nodes)   <- node B left
LADDER UP:   model -> qwen2.5-1.5b... (pool 1700 MB across 2 nodes)   <- node B returned
```

A request submitted after every transition was served (per-request model
loading means there is no warm state to lose). The window where no tier fits —
too little pledged memory for even the smallest catalog model — is a known
gap.

## B. The polite node — the owner always comes first

Node B ran with the busy sensor on (`--busy-at 45`). The proof started a CPU
hog at normal priority (simulating the owner launching a game):

- B's sensor measured **other-process** CPU load (its own rpc-server's usage
  subtracted), saw sustained load, **drained out by itself** (`/leave` + its
  rpc-server stopped — all memory returned), with zero penalty.
- The network **kept serving during the "game"** — the surviving node answered
  alone on the small model (slower under contention, and that's correct:
  the rpc-server runs at BELOW_NORMAL OS priority, so the owner's game eats
  first).
- Hog terminated → machine calm → B **rejoined by itself** → ladder back up.

## C. Weighted fairness — memory lent == share earned

Pledges were 1000 MB (amina) vs 700 MB (bilal). llama.cpp's `--tensor-split`
was driven by the pledges, and the 29 layers of the 1.5B model split **17 vs
12** — matching the pledge ratio. Credits per token followed the layer share,
from bilal's wallet statement in the transcript:

```
+9.517  served 12/29 layers of qwen2.5-1.5b... for 23 tokens via jeddah-b
+9.517  served 12/29 layers of qwen2.5-1.5b... for 23 tokens via jeddah-b
balance before leaving: 9.517   after rejoining: 19.034
```

Withdrawal is never punished: the balance survived leaving in full.

## Still not proven (updated)

Everything in Part 1's "what this does NOT prove" list still stands
(single machine, trusted nodes, small models, no privacy) — minus per-request
politeness and dynamic capacity, which are now real.

---

# Part 3 — It Works (v0.3)

**Date:** 2026-08-08 · **Status:** PASS · **Raw transcript:** [net/proof/artifacts/it-works-2026-08-08.txt](../net/proof/artifacts/it-works-2026-08-08.txt) · **Reproduce:** [net/proof/run_it_works.py](../net/proof/run_it_works.py)

Parts 1 and 2 showed the network is *correct*. This one shows it is *usable* —
and it runs over the machine's real LAN address (192.168.0.188), not loopback,
so the traffic crosses the actual network stack and the nodes advertise
dialable addresses.

## A. The pipeline is resident — the reload is gone

v0.2 spawned a fresh engine per request, re-streaming the model to every node
before a single token could appear. v0.3 keeps a `llama-server` alive holding
the sharded pipeline. Measured in the same run:

```
COLD  first token after 5.17s   (32 tokens, 57.92 tok/s)  engine_warm=False
WARM  first token after 0.07s   (32 tokens, 43.21 tok/s)  engine_warm=True
-> time-to-first-token improved 75.0x once the pipeline was resident
```

(Cold-start cost varies with disk cache and machine load — repeat runs measured
between 5.2s and 7.3s, i.e. 75x–99x. The warm figure was stable at ~0.07s. The
committed transcript is the 75x run; the assertion is that warm beats cold, not
a particular multiple.)

The engine rebuilds only when the pipeline actually changes — a node joining or
leaving, or the capacity ladder moving tier. The proof asserts zero restarts
across the two warm requests, then ≥1 restart after a second node joins.

## B. Tokens stream

`POST /ask/stream` emits server-sent events as generation happens. From the
captured run: **32 chunks spread over 0.91s**, first at 0.10s and last at
1.01s — not one lump at the end. The proof asserts the streamed text matches
the final answer exactly.

## C. A person can use it

`GET /` serves a chat page from the coordinator. It was verified by driving it
in a real browser: typing a question, watching tokens arrive, and reading the
per-answer footer that names the nodes that served it
(`RPC0 192.168.0.188:50120 layers 0-24`), the model, the speed, whether the
pipeline was warm, and which operators were credited.

## D. The ladder still holds, with the engine following it

A second node joined → the ladder upgraded to the 1.5B model → the engine
rebuilt for the new two-node pipeline (layers **0–11** and **12–28**) → the
following request was warm again at 0.15s. Credits stayed weighted by layer
share (amina 1000 MB pledge earned more than bilal's 700 MB).

## What Part 3 still does not prove

- **Still one physical machine.** Processes now talk over the real LAN
  interface with dialable addresses, which is the last step before two houses —
  but it is not two houses yet.
- The trust, privacy, and scale caveats from Part 1 are unchanged: trusted
  operators, nodes can reconstruct prompts from activations, small models.
- The engine is coordinator-local by design in v0 (`llama-server` binds
  loopback); nodes are the distributed part.

---

# Part 4 — Usable Together (v0.4)

**Date:** 2026-08-08 · **Status:** PASS · **Raw transcript:** [net/proof/artifacts/v04-2026-08-08.txt](../net/proof/artifacts/v04-2026-08-08.txt) · **Reproduce:** [net/proof/run_v04.py](../net/proof/run_v04.py)

Part 3 made the network fast. This one makes it something a group of people can
actually share: joinable in one command, able to hold a conversation, able to
serve several people at once, and unable to lose anyone's contribution.

## A. Joining takes one command and no address

The node in this proof is started with **no `--coordinator`, no IP, no port** —
only `--discover`. It broadcasts on the local network, the coordinator answers
with an address reachable from the asker's side, and the node registers:

```
[sanad-node] looking for a coordinator on this network...
[sanad-node] found 'sanad-proof' at http://192.168.0.188:7866
[riyadh-a] SERVING on 192.168.0.188:50140 (pledge 1000 MB, low OS priority, operator amina)
```

Discovery is broadcast-only, so it never leaves the subnet, and it is a
convenience rather than a trust mechanism: anyone on your LAN could answer, so
pass an explicit `--coordinator` on networks you do not trust.

## B. It holds a conversation

Requests now carry the whole thread, and llama-server applies the model's own
chat template (`--jinja`). Both matter: the template is what makes an instruct
model answer rather than ramble a raw continuation, and the history is what
makes turn 2 possible.

```
turn 1 -> "Hello Abdullah! It's a pleasure to meet you..."
turn 2 -> "Your name is Abdullah and your project is called Sanad."
```

The proof asserts both facts are recalled — established only in turn 1, needed
only in turn 2.

## C. Credits survive an outage

The ledger is now append-only on disk, fsynced per entry. The proof kills the
coordinator mid-flight and checks the balances three ways:

```
balances before:                 {'amina': 162.0}
replayed from the file alone:    {'amina': 162.0}   (6 entries, self-consistent)
balances after restart:          {'amina': 162.0}
```

The middle line is the important one: the file was replayed by a *separate
process, with the coordinator dead*. That is what makes "append-only and
auditable" a fact rather than a claim — and it is what GOVERNANCE.md's promise
that contribution is never erased actually requires. `GET /ledger/audit`
recomputes every balance from the entries on demand.

## D. Several people at once

The engine runs with parallel slots and continuous batching, and the
coordinator admits up to `--concurrency` jobs at a time:

```
individual durations: [2.61, 1.22, 2.61, 1.35]
wall clock for all four: 2.62s   (sum if served one-by-one: 7.80s)
-> 3.0x more throughput
```

A note the tests now encode: **priority only binds under contention.** With
free slots, everyone is admitted immediately and credit order is moot; the
queue tests therefore run against a deliberately single-slot server.

## What Part 4 still does not prove

- **Still one physical machine.** Every proof so far runs processes on one box
  over the real LAN interface. Two machines remains the next milestone.
- **Still centrally coordinated.** The compute is distributed; the control
  plane is not. One coordinator holds the registry, ledger, and queue — this is
  the AI Horde model, stated plainly, not the blockchain one. Federation
  (several coordinators recognizing each other) is the intended path to
  decentralising control without a token; it is not built.
- Trust, privacy, and scale caveats from Part 1 are unchanged.

## Next milestone

Two nodes on **two different machines on two different networks**, same proofs
— then federation, then the audit hooks from the roadmap.


---

# Part 5 — Two Networks (v0.5)

**Date:** 2026-08-08 · **Status:** PASS · **Raw transcript:** [net/proof/artifacts/two-networks-2026-08-08.txt](../net/proof/artifacts/two-networks-2026-08-08.txt) · **Reproduce:** `python net/proof/run_two_networks.py` (needs Docker)

Parts 1–4 all carried the same caveat: *still one machine*. This one removes
most of it. Each node runs in its own Linux container on its own Docker
network, with its own IP and its own network namespace. The coordinator is
multi-homed across both; the nodes are not.

## A. The networks really are separate

```
node-a lives at 172.28.0.3
node-b lives at 172.29.0.3
node-a trying to reach node-b directly -> socket.gaierror: Name or service not known
```

node-a cannot even *resolve* node-b, let alone connect to it. Only the
coordinator bridges the two — which is precisely Sanad's topology, now enforced
by the network rather than by convention.

## B. Real sharded inference across them

```
RPC0  node-a:50070  layers 0-17  (18 layers)
RPC1  node-b:50070  layers 18-28  (11 layers)
text: A mining pool is a type of decentralized cryptocurrency mining service...
```

The 1.5B model is split across two isolated networks and answers correctly.
This also exercises the **Linux** node path — `/proc` CPU sensing, `nice(10)`,
no `.exe` suffix — which every earlier proof skipped.

## C. The WAN cost, measured rather than assumed

40 ms of latency injected on each link with `tc netem`:

```
local-network baseline: 31.3 tok/s
under WAN latency:       4.2 tok/s  (first token 4.26s)
-> 7.5x slower
```

This is the number that matters most, and it is not flattering. Splitting a
model across a network means **every token crosses every hop**, so latency
multiplies by chain length. It is the honest measurement of the tension at the
centre of this design: the models that most need sharding are the ones that
shard worst. Sanad exists to run models **too big for one device** — not to be
fast. Anyone who wants speed should use a centralized provider, and our own
documentation says so.

## D. Credits, weighted across networks

`{'amina': 109.9, 'bilal': 67.1}` — node-a held 18 of 29 layers and earned
proportionally more. Memory lent equals share earned, across a network
boundary.

## What Part 5 still does not prove

- **Not two ISPs.** Separate networks and namespaces on one physical host is
  not the same as two homes, two routers, and public routing. NAT traversal and
  real internet paths remain untested. [net/deploy/README.md](../net/deploy/README.md)
  documents the free way to do that (a no-cost cloud VM joined by a private
  WireGuard mesh) and why the RPC port must never face the open internet.
- **Still centrally coordinated.** Compute is distributed; the control plane is
  not. Federation is the intended path and is not built.
- Trust and privacy caveats from Part 1 are unchanged.

## Next milestone

The same proof with the second node on a free cloud VM across the public
internet — the guide is written, the run is not yet recorded.
