# Running Sanad across networks

Two ways to get a second node that is genuinely on the other side of a network,
without buying a machine. The first runs on your own computer in minutes; the
second crosses the real internet and is free.

---

## 1. Two isolated networks, on one computer (Docker)

Each node runs in its own Linux container on its own Docker network, with its
own IP and network namespace. The coordinator is the only thing that can reach
either node — **the nodes cannot even resolve each other**, which is exactly
Sanad's topology. Traffic crosses a routed TCP hop, not shared memory.

```bash
cd net && python -m sanad_net.setup --yes   # the containers use these model files
cd .. && python net/proof/run_two_networks.py
```

That builds the images, brings up the stack, proves the isolation, runs sharded
inference across both networks, injects 40 ms of WAN latency with `tc netem` to
measure the honest cost, checks credits, and tears everything down. Recorded
run: [two-networks transcript](../proof/artifacts/two-networks-2026-08-08.txt).

To just run it and chat:

```bash
docker compose -f net/deploy/docker-compose.yml up --build
# then open http://localhost:7860/
```

This also exercises the **Linux** node path — `/proc` CPU sensing, `nice(10)`,
no `.exe` suffix — which the Windows proofs never touch.

What it proves: the software works across separate networks and survives WAN
latency. What it does **not** prove: NAT traversal, two ISPs, or public
routing. For that, use the next section.

---

## 2. A second node on the real internet, free

The honest version of "two machines on two networks". Verified as available in
August 2026 — free tiers change, so re-check before relying on it.

**The shape:** a free cloud VM runs the node; your machine runs the
coordinator; the two are joined by a private WireGuard mesh so they can reach
each other without either opening a public port.

That last part is not optional. `ggml-rpc-server` has **no authentication** and
has had remote-code-execution CVEs (see `MIN_LLAMA_BUILD` in
[`sanad_net/node.py`](../sanad_net/node.py)). It must never be exposed to the
open internet. A private mesh gives both ends stable addresses that only your
own devices can reach, which is what makes this safe to do at all.

### Steps

**a. Get a free VM.** Options, easiest signup first (verified August 2026 —
free tiers move, so re-check):

| Host | Signup | RAM | Notes |
|---|---|---|---|
| [Segfault](https://www.thc.org/segfault) | **none at all** — `ssh root@segfault.net` (password `segfault`) | ~2 GB (confirm with `cat /sys/fs/cgroup/memory.max` on login) | root Linux, no email, no card; keep a `tmux` session alive so it isn't reclaimed. Ask in their ops channel before running many nodes — it's a donated service. |
| Oracle Cloud Always Free | account + card (identity only) | 12 GB (Ampere ARM) | does not expire; ARM needs a source build |
| Google Cloud `e2-micro` | account + card | 1 GB (x86-64) | enough for one shard |

Avoid the "free VPS, no card" listicles — those hosts are abused within hours
and effectively don't exist. And do **not** try to run nodes inside GitHub
Actions or Google Colab: both forbid it in writing and you can lose the whole
account.

**b. Connect the node to your coordinator.** Two ways, depending on how
account-averse you are.

*Private mesh (needs a free account, gives stable addresses):*
[Tailscale](https://tailscale.com) is free for personal use and needs no
inbound firewall rules on either side:

```bash
# on the VM
curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up
# on your own machine: install Tailscale, sign in to the same account
tailscale ip -4        # note the VM's 100.x address
```

*No account at all (the node dials out to your coordinator):* have the node
open an outbound tunnel and bind the rpc-server to loopback, so the auth-less
port is never exposed. [gsocket](https://gsocket.io) needs no account, no card,
and traverses any NAT:

```bash
# on the node: rpc-server on localhost only, reached through the tunnel
gs-netcat -l -d 127.0.0.1 -p 50052 -s <a-secret-you-invent>
# on your coordinator's machine: expose it locally
gs-netcat -p 50052 -s <the-same-secret>   # now localhost:50052 reaches the node
```

This flips the requirement from "reachable inbound" (rare) to "outbound
allowed" (nearly universal), which is what makes zero-account hosts usable.
Plain `ssh -R 50052:localhost:50052 you@your-coordinator` does the same with no
third party if you have an SSH host.

**c. Get llama.cpp on the VM.** On an x86-64 VM, let Sanad fetch it:

```bash
git clone https://github.com/A-Alwabel/sanad && cd sanad/net
python3 -m sanad_net.setup --yes --models small
```

On an **ARM** VM there is no prebuilt release, so build it — and note the
binary must end up named `ggml-rpc-server`, which is what the node looks for:

```bash
sudo apt-get update && sudo apt-get install -y build-essential cmake git python3
git clone https://github.com/ggml-org/llama.cpp && cd llama.cpp
cmake -B build -DGGML_RPC=ON -DLLAMA_CURL=OFF
cmake --build build --config Release -j --target rpc-server llama-server
mkdir -p ~/sanad/.local/bin
cp build/bin/rpc-server ~/sanad/.local/bin/ggml-rpc-server   # the name matters
cp build/bin/llama-server build/bin/*.so* ~/sanad/.local/bin/ 2>/dev/null || true
```

**d. Run the node on the VM**, bound to the mesh address — never `0.0.0.0`:

```bash
git clone https://github.com/A-Alwabel/sanad && cd sanad/net
python3 -m sanad_net.node --node-id cloud-a --operator you \
    --host <the-100.x-address> --advertise <the-100.x-address> --port 50070 \
    --pledge-mb 1000 --busy-at 101 \
    --rpc-bin ~/llama.cpp/build/bin \
    --coordinator http://<your-machine's-100.x-address>:7860
```

**e. Run the coordinator at home**, bound to your own mesh address:

```bash
python -m sanad_net.coordinator --port 7860 --bind <your-100.x-address> \
    --models <small.gguf>,<large.gguf> --llama-bin ../.local/bin \
    --ledger ../.local/ledger.jsonl
```

Ask it something. The answer crosses two ISPs and the public internet, and the
shard map under it will name a machine in a datacentre you have never touched.

### What to expect

Slower than the LAN, by a lot. The container proof measures 31.3 → 4.2 tok/s
when 40 ms of latency is added to each link, and a real internet path is often
worse. That is the physics of splitting a model across a network: every token
crosses every hop. Sanad exists to run models **too big for one device**, not
to be fast — see [docs/PROOF.md](../../docs/PROOF.md) and the "What Sanad is
not" section of the README.

### Cautions

- Oracle reclaims idle Always Free instances; keep the node actually serving.
- `--busy-at 101` marks a node dedicated (no owner to yield to). On a machine
  you actually use, leave the busy sensor on.
- Keep the mesh private. If you find yourself opening a port on a public IP to
  make this work, stop: that is the configuration the CVE guard exists to
  prevent.
