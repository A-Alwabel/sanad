# First Light — proof that Sanad works

**Date:** 2026-08-05 · **Status:** PASS · **Raw transcript:** [net/proof/artifacts/first-light-2026-08-05.txt](../net/proof/artifacts/first-light-2026-08-05.txt) · **Reproduce:** [net/proof/run_first_light.py](../net/proof/run_first_light.py)

On 2026-08-05, a real Sanad network served real LLM inference for the first
time: a model's layers physically split across two separate node processes over
TCP, requests flowing through the chain, and the credit ledger doing exactly
what [GOVERNANCE.md](../GOVERNANCE.md) promises — contribution buys priority,
and nobody is ever locked out.

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

Layers 0–12 lived in one node's process, layers 13–24 in the other's. Neither
process ever held the whole model. During generation, activations crossed TCP
between them for every token.

### 2. Real text came out the other end

Prompt (anonymous user): `A mining pool is` →

> "a network of servers that work together to perform complex calculations and
> data processing tasks. They are often used in the mining industry to increase
> the speed and efficiency of mining"

31 decode tokens at **~37–44 tok/s** across runs (CPU-only, two-node chain,
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
- **Not efficient:** the model reloads per request (~4 s overhead); a resident
  pipeline is next.
- **Not big:** 0.5B parameters. Chosen so the proof is cheap to reproduce
  anywhere. The identical mechanism loads 70B-class models across enough nodes
  (llama.cpp splits by each node's reported free memory).

## Reproduce it yourself

```powershell
cd net
python -m unittest discover -s tests -v        # 7 unit tests, no binaries needed
python proof/run_first_light.py --llama-bin ../.local/bin `
    --model ../.local/models/qwen2.5-0.5b-instruct-q4_k_m.gguf
```

Setup for the binaries and model: [net/README.md](../net/README.md). The
script asserts every claim above and exits with `FIRST LIGHT: PASS`.

## Next milestone

Two nodes on **two different machines on two different networks**, same proof
— then a resident pipeline, then the audit hooks from the roadmap.
