# sanad_net — the real network layer (Phase 1, first light)

This is Sanad running **for real**: a GGUF model's layers are physically split
across separate node processes that communicate over TCP, with a Sanad
coordinator assembling the chain and accounting non-tradeable credits.

It was first proven working on 2026-08-05 — see [docs/PROOF.md](../docs/PROOF.md)
and the captured transcript in [proof/artifacts/](proof/artifacts/).

## What it is

- **Inference engine:** llama.cpp's RPC mode (`ggml-rpc-server` + `llama-completion --rpc`),
  exactly the "llama.cpp RPC adapter" named in [docs/ROADMAP.md](../docs/ROADMAP.md).
  llama.cpp streams each node's layer shard to it over TCP at load time and
  pipelines activations through the nodes during generation.
- **Sanad's layer (this package, stdlib-only Python):**
  - `sanad_net.coordinator` — node registry (heartbeats + TTL), credit ledger,
    credit-priority job queue, HTTP API (`/register`, `/heartbeat`, `/ask`, `/status`),
    and a shard-map parser that extracts the per-node layer assignment as proof.
  - `sanad_net.node` — wraps one `ggml-rpc-server` process and registers it
    with the coordinator under an operator account that earns its credits.
  - `sanad_net.client` — tiny CLI (`ask`, `status`).

## Honest scope (read this)

- This first light ran on **one physical machine** — multiple OS processes
  talking over local TCP. The protocol is network-transparent (the same flags
  accept remote `host:port`), but the multi-machine, multi-network run is the
  next milestone, not this one.
- llama.cpp's RPC backend is upstream-labeled **"fragile and insecure — never
  run the rpc-server on an open network"**. Trusted machines only, for now.
  This matches the permissioned-first trust model in
  [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md).
- Each request currently reloads the model (~seconds of overhead): simple and
  stateless, but wasteful. A resident pipeline (llama-server or engine adapters)
  is listed in next steps.
- Credits split evenly across serving nodes; per-layer-share weighting is
  modeled in the [simulation](../prototype/) and is future work here.

## Setup (Windows)

```powershell
# 1. llama.cpp binaries (build b10276 or later; includes ggml-rpc-server.exe)
#    from https://github.com/ggml-org/llama.cpp/releases
#    -> unzip into ..\.local\bin
# 2. A small GGUF model, e.g.:
#    https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF (q4_k_m, ~470 MB)
#    -> save into ..\.local\models
```

Linux/macOS work the same way with the matching llama.cpp release archives
(binary names lose the `.exe`; the node wrapper's process handling is
Windows-tested only so far — reports welcome).

## Run it

```powershell
cd net

# terminal 1 — coordinator
python -m sanad_net.coordinator --port 7860 `
    --model ../.local/models/qwen2.5-0.5b-instruct-q4_k_m.gguf --llama-bin ../.local/bin

# terminals 2 & 3 — two nodes (in real life: two different people's machines)
python -m sanad_net.node --node-id riyadh-a --operator amina --port 50070 --rpc-bin ../.local/bin --coordinator http://127.0.0.1:7860
python -m sanad_net.node --node-id jeddah-b --operator bilal --port 50071 --rpc-bin ../.local/bin --coordinator http://127.0.0.1:7860

# terminal 4 — ask through the chain
python -m sanad_net.client --coordinator http://127.0.0.1:7860 ask --user amina "What is a mining pool?"
python -m sanad_net.client --coordinator http://127.0.0.1:7860 status
```

## Reproduce the proof

```powershell
cd net
python proof/run_first_light.py --llama-bin ../.local/bin `
    --model ../.local/models/qwen2.5-0.5b-instruct-q4_k_m.gguf
```

The script starts the whole network, proves layer sharding, runs real inference
for three users, proves credit priority (a contributor queued *after* an
anonymous user is served *before* him — and the anonymous user is still
served), and prints `FIRST LIGHT: PASS`. It cleans up all child processes.

## Tests

```powershell
cd net
python -m unittest discover -s tests -v   # no binaries or model needed
```
