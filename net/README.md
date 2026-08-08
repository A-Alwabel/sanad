# sanad_net — the real network layer (v0.4, "usable together")

This is Sanad running **for real**: a GGUF model's layers are physically split
across separate node processes that communicate over TCP, with a Sanad
coordinator assembling the chain, streaming the answer back, and accounting
non-tradeable credits.

Proven in four recorded milestones — First Light, the Living Network, It Works,
and Usable Together — see [docs/PROOF.md](../docs/PROOF.md) and the captured
transcripts in [proof/artifacts/](proof/artifacts/).

**Use it in a browser:** start a coordinator and one node (below), then open the
coordinator's address. You get a chat page that shows, under every answer, which
nodes served which layers, how fast, and who earned the credits.

## What v0.4 adds

- **One-command join** — `--discover` finds the coordinator by LAN broadcast;
  no address to type. Broadcast-only (never routed off the subnet) and a
  convenience, not a trust mechanism: pass `--coordinator` explicitly on
  networks you don't trust.
- **Conversations** — `/ask` and `/ask/stream` accept a `messages` list
  (OpenAI-style roles), and the engine applies the model's own chat template
  (`--jinja`). Single `prompt` still works and becomes one user message.
- **Durable credits** — `--ledger path.jsonl` makes the ledger append-only on
  disk, fsynced per entry and replayed at startup, so contribution survives a
  restart or crash. `GET /ledger/audit` recomputes every balance from the
  entries; anyone with the file can repeat the check.
- **Concurrency** — `--concurrency N` (default 4) serves N requests at once via
  engine slots and continuous batching. Note that credit priority only binds
  when slots are scarce; with free slots everyone is admitted immediately.

## What v0.3 added

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

- Everything so far has run on **one physical machine**. The proofs bind to the
  machine's real LAN address and nodes advertise dialable addresses — the last
  step before two houses — but two machines on two networks is still the next
  milestone, not a claim.
- **The compute is distributed; the coordination is not.** One coordinator holds
  the registry, ledger, and queue. This is the AI Horde model, not the
  blockchain one — there is no consensus layer and no token. Federation (several
  coordinators recognising each other) is the intended path to decentralising
  control; it is not built.
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

## Get started

Two commands. You need Python 3.12+ and about 1.6 GB of disk.

```bash
cd net
python -m sanad_net.setup     # fetches llama.cpp for your machine + two small models
python -m sanad_net.run       # starts a network and opens the chat page
```

`setup` tells you exactly what it will download and how big it is before it
starts, puts everything in `.local/` next to the repo, and installs nothing
system-wide — delete that folder to undo all of it. It picks the right build
for Windows / macOS / Linux and x64 / ARM automatically, and refuses anything
older than the llama.cpp release that fixed CVE-2026-34159.

`run` starts a coordinator and one node on your machine, waits until the
network is ready, and opens your browser. Ctrl-C stops everything it started.
Useful flags:

```bash
python -m sanad_net.run --name amina        # the account your contribution is credited to
python -m sanad_net.run --pledge-mb 4000    # lend more memory (default 1500)
python -m sanad_net.run --dedicated         # a spare box: never pause for an "owner"
python -m sanad_net.run --no-browser
```

### Let someone else join

On their machine, after the same `setup`:

```bash
python -m sanad_net.run --join
```

That finds your coordinator on the local network by broadcast — no address to
type. If they are somewhere else, give them the address instead:
`--join http://192.168.0.10:7860`. Their credits are theirs, and are kept if
they leave.

### Running the parts by hand

`run` is a convenience; every piece is a normal module you can start yourself,
which is what you want on a server or when the defaults do not fit:

```bash
python -m sanad_net.coordinator --port 7860 --bind 0.0.0.0     --models ../.local/models/qwen2.5-0.5b-instruct-q4_k_m.gguf,../.local/models/qwen2.5-1.5b-instruct-q4_k_m.gguf     --llama-bin ../.local/bin --ledger ../.local/ledger.jsonl

python -m sanad_net.node --node-id riyadh-a --operator amina --port 50070     --pledge-mb 1000 --busy-at 101 --discover --rpc-bin ../.local/bin

python -m sanad_net.client --coordinator http://192.168.0.10:7860 ask --user amina "What is a mining pool?"
python -m sanad_net.client --coordinator http://192.168.0.10:7860 statement --user amina
```

## Reproduce the proofs

```powershell
cd net
python proof/run_v04.py               # v0.4: one-command join, memory, durable credits, concurrency
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
