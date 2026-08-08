# sanad_net — the real network layer (v0.3, "it works")

This is Sanad running **for real**: a GGUF model's layers are physically split
across separate node processes that communicate over TCP, with a Sanad
coordinator assembling the chain, streaming the answer back, and accounting
non-tradeable credits.

Proven in three recorded milestones — First Light, the Living Network, and It
Works — see [docs/PROOF.md](../docs/PROOF.md) and the captured transcripts in
[proof/artifacts/](proof/artifacts/).

**Use it in a browser:** start a coordinator and one node (below), then open the
coordinator's address. You get a chat page that shows, under every answer, which
nodes served which layers, how fast, and who earned the credits.

## What v0.3 does

- **Resident pipeline** — a `llama-server` holds the sharded model in the nodes'
  memory and stays alive. The first question builds the pipeline; every later
  one reuses it. Measured: time-to-first-token **5.17s → 0.07s**. The engine is
  rebuilt only when the pipeline actually changes (a node joins or leaves, or
  the ladder moves tier).
- **Streaming** — `POST /ask/stream` emits server-sent events as tokens are
  generated. `POST /ask` still returns one JSON blob if you prefer.
- **Chat page** — `GET /` serves a UI anyone can use without a terminal.
- **Capacity ladder** — give the coordinator a catalog
  (`--models a.gguf,b.gguf`); it always serves the largest model the pledged
  memory pool can hold, upgrading and downgrading as nodes come and go.
- **Polite node** — `--pledge-mb` declares the RAM you lend; the rpc-server runs
  at low OS priority; the busy sensor (`--busy-at`/`--resume-at`) drains the
  node out when the owner needs the machine and rejoins when things calm down.
  `--busy-at 101` = dedicated node. Withdrawal keeps all credits.
- **Weighted fairness** — layer shares follow pledges (via `--tensor-split`,
  verified empirically), and each token's credit is split by the layers each
  node actually held.
- **Anti-starvation queue** — anonymous users are served within bounded time:
  every third queue slot is strictly first-come-first-served regardless of
  credits; the rest go to contributors first.
- **Escrow accounting** — a job's expected cost is escrowed at submit and
  settled after the run, so queued jobs can't all borrow the same balance, and
  cancelled, timed-out, or failed jobs are refunded.
- **Chain repair** — a job that fails because a node vanished evicts the stale
  nodes, rebuilds the engine, and retries once against the survivors.
- **Wallet-style statement** — `client statement --user <name>` prints the
  earn/spend history from `/ledger`. Audited in the append-only-ledger sense;
  client identity is unauthenticated in v0 — a trusted-client assumption, like
  the trusted-node one.

## What it is

- **Inference engine:** llama.cpp — `ggml-rpc-server` on each node holds a slice
  of the layers, and a coordinator-local `llama-server --rpc` drives the
  pipeline and streams tokens out.
- **Sanad's layer (this package, stdlib-only Python):**
  - `sanad_net.engine` — `EngineManager` keeps exactly one resident engine alive
    for the current pipeline, captures the shard map from its start-up log, and
    streams completions.
  - `sanad_net.coordinator` — node registry (heartbeats + TTL), credit ledger,
    credit-priority job queue, capacity ladder, HTTP API (`/`, `/ask`,
    `/ask/stream`, `/register`, `/heartbeat`, `/leave`, `/status`, `/ledger`).
  - `sanad_net.node` — wraps one `ggml-rpc-server`, registers it under an
    operator account, and runs the politeness state machine.
  - `sanad_net.client` — CLI (`ask`, `status`, `statement`).
  - `sanad_net.webui.html` — the chat page.

## Platforms

| | Binaries | CPU sensing | Low priority |
|---|---|---|---|
| **Windows** | `.exe` suffix | kernel32 `GetSystemTimes`/`GetProcessTimes` (exact, per-process) | `BELOW_NORMAL_PRIORITY_CLASS` |
| **Linux** | no suffix | `/proc/stat` + `/proc/<pid>/stat` (exact, per-process) | `nice(10)` |
| **macOS/BSD** | no suffix | `os.getloadavg()` (coarse, whole-machine) | `nice(10)` |

Sensing never shells out to a subprocess: an early version used PowerShell and
starved under exactly the load it was meant to detect. A native darwin sampler
is welcome contributor work. Windows is the most-tested path; Linux and macOS
are implemented but have not had a recorded milestone run.

## Honest scope (read this)

- Everything so far has run on **one physical machine**. v0.3's proof binds to
  the machine's real LAN address and nodes advertise dialable addresses — the
  last step before two houses — but two machines on two networks is still the
  next milestone, not a claim.
- llama.cpp's RPC backend is upstream-labeled **"fragile and insecure — never
  run the rpc-server on an open network"**. Trusted machines only, for now.
  This matches the permissioned-first trust model in
  [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md).
- The coordinator has **no authentication** in v0: any client that can reach it
  may ask as any account name. Trusted networks only.
- The coordinator's machine holds the full GGUF and streams each node its shard
  when the engine starts. Distributing model storage is future work.
- Nodes can reconstruct prompts from the activations passing through them — an
  unsolved problem across this whole field, not just here.

## Setup

```powershell
# 1. llama.cpp binaries (build b10276 or later; needs ggml-rpc-server + llama-server)
#    from https://github.com/ggml-org/llama.cpp/releases  ->  unzip into ..\.local\bin
# 2. A small GGUF model, e.g. Qwen2.5-0.5B-Instruct q4_k_m (~470 MB)
#    https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF  ->  save into ..\.local\models
```

## Run it

```powershell
cd net

# terminal 1 — coordinator with a capacity-ladder catalog (small,large).
# --bind 0.0.0.0 lets nodes and browsers on your network reach it.
python -m sanad_net.coordinator --port 7860 --bind 0.0.0.0 `
    --models ../.local/models/qwen2.5-0.5b-instruct-q4_k_m.gguf,../.local/models/qwen2.5-1.5b-instruct-q4_k_m.gguf `
    --llama-bin ../.local/bin

# terminals 2 & 3 — nodes (in real life: two different people's machines).
# Use each machine's own address for --coordinator.
python -m sanad_net.node --node-id riyadh-a --operator amina --host 0.0.0.0 --port 50070 `
    --pledge-mb 1000 --busy-at 101 --rpc-bin ../.local/bin --coordinator http://192.168.0.10:7860
python -m sanad_net.node --node-id jeddah-b --operator bilal --host 0.0.0.0 --port 50071 `
    --pledge-mb 700 --rpc-bin ../.local/bin --coordinator http://192.168.0.10:7860

# then: open http://192.168.0.10:7860/ in a browser and start typing.
# or from a terminal:
python -m sanad_net.client --coordinator http://192.168.0.10:7860 ask --user amina "What is a mining pool?"
python -m sanad_net.client --coordinator http://192.168.0.10:7860 status
python -m sanad_net.client --coordinator http://192.168.0.10:7860 statement --user amina
```

## Reproduce the proofs

```powershell
cd net
python proof/run_it_works.py          # v0.3: resident pipeline, streaming, chat UI, real LAN
python proof/run_living_network.py    # v0.2: capacity ladder, polite node, weighted credits
python proof/run_first_light.py       # v0.1: sharded inference + credit priority
```

Each script starts the whole network, asserts its claims, prints a transcript,
and cleans up its child processes.

## Tests

```powershell
cd net
python -m unittest discover -s tests -v   # no binaries or model needed
```
