# Proofs that Sanad works

Two milestones, both achieved 2026-08-05, both fully reproducible:

| Milestone | What it proves | Transcript | Reproduce |
|---|---|---|---|
| **Part 1 — First Light** (v0.1) | Real sharded inference + live credit economy | [first-light transcript](../net/proof/artifacts/first-light-2026-08-05.txt) | [run_first_light.py](../net/proof/run_first_light.py) |
| **Part 2 — The Living Network** (v0.2) | Capacity ladder + polite node + weighted fairness | [living-network transcript](../net/proof/artifacts/living-network-2026-08-05.txt) | [run_living_network.py](../net/proof/run_living_network.py) |

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

## Next milestone

Two nodes on **two different machines on two different networks**, same proofs
— then a resident pipeline, then the audit hooks from the roadmap.
