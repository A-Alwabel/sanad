"""Runnable Sanad v0 demo.

From the prototype/ directory:

    python -m sanad.demo

Scenario: an 80-layer mock "big-70b" (40 GiB at ~4-bit) that NO single
worker can hold; six small workers (8-24 GiB) form a pipeline; one
over-claiming worker is rejected; three clients — one anonymous, two with
earned credits — submit jobs and the queue proves contributors get
priority while the anonymous user is still served.
"""

import asyncio

from sanad.coordinator import Coordinator
from sanad.ledger import CreditLedger
from sanad.models import GiB, Job, ModelSpec, WorkerInfo
from sanad.worker import MockWorker

TIME_SCALE = 0.05  # sleep 5% of simulated latency so the demo streams visibly


def _rule(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 58 - len(title)))


def main() -> None:
    print("Sanad v0 simulation - pipeline sharding + non-transferable credits")
    print("(mock computation, stdlib only; real backends are Phase 1 work)")

    model = ModelSpec("big-70b", n_layers=80, bytes_per_layer=GiB // 2)
    ledger = CreditLedger()
    coord = Coordinator(model, ledger)

    _rule(f"Model: {model.name}")
    print(f"{model.n_layers} layers x 0.5 GiB = {model.total_bytes / GiB:.0f} GiB total")
    print("Largest worker below has 24 GiB -> no single worker can host it.")

    _rule("Worker registration")
    fleet = [
        # (id, vram GiB, layer range, per-hop latency ms)
        ("riyadh-a",  12, (0, 16),  35.0),
        ("cairo-b",    8, (16, 30), 60.0),
        ("berlin-c",  16, (16, 32), 45.0),
        ("jakarta-d", 24, (32, 56), 80.0),
        ("toronto-e", 16, (56, 72), 50.0),
        ("lagos-f",    8, (72, 80), 70.0),
        ("greedy-g",   8, (0, 64),  30.0),  # over-claims: 64 layers need 32 GiB
    ]
    for wid, vram, rng, lat in fleet:
        info = WorkerInfo(wid, vram * GiB, rng, lat)
        try:
            coord.register(MockWorker(info, ledger, TIME_SCALE))
            print(f"  registered  {wid:<10} {vram:>3} GiB   layers {rng[0]:>2}-{rng[1]:<3} {lat:>4.0f} ms/hop")
        except ValueError as exc:
            print(f"  REJECTED    {wid:<10} ({exc})")

    _rule("Pipeline map (lowest-latency contiguous cover)")
    stages = coord.build_pipeline()
    for i, stage in enumerate(stages, 1):
        info = stage.worker.info
        a, b = stage.layers
        print(f"  stage {i}: {info.id:<10} layers {a:>2}-{b:<3} ({b - a:>2})  {info.latency_ms:>4.0f} ms")
    total_ms = coord.pipeline_latency_ms(stages)
    print(f"  per-token path latency: {total_ms:.0f} ms -> ~{1000 / total_ms:.1f} tok/s single-stream")
    print("  (deliberately sober: WAN RTT is the hard cap on swarm speed)")
    idle = sorted(set(coord.workers) - {s.worker.info.id for s in stages})
    if idle:
        print(f"  not selected this round (higher-latency alternatives): {', '.join(idle)}")

    _rule("Clients")
    # Contributors earned credits in earlier sessions by running workers;
    # earn() is the ONLY way credits come into existence.
    ledger.earn("amina", tokens=300, weight=1.0)
    ledger.earn("bilal", tokens=150, weight=1.0)
    for who in ("amina", "bilal", "anon"):
        print(f"  {who:<6} balance: {ledger.balance(who):6.1f} credits")

    _rule("Job queue (submitted anon -> bilal -> amina)")
    jobs = [
        Job("job-anon",  "anon",  "what is sanad",            max_tokens=10),
        Job("job-bilal", "bilal", "explain pipeline sharding", max_tokens=10),
        Job("job-amina", "amina", "why non-tradeable credits", max_tokens=10),
    ]
    for job in jobs:
        coord.submit(job)
    print("  scheduled order (credit priority, arrival breaks ties):")
    for rank, job in enumerate(coord.queue_order(), 1):
        print(f"    {rank}. {job.id:<10} requester={job.requester:<6} "
              f"balance={ledger.balance(job.requester):6.1f}")
    print("  -> contributors jump ahead; anonymous is served last, not never.")

    _rule("Streaming (mock tokens)")

    def on_token(job: Job, token: str) -> None:
        if len(job.tokens) == 1:
            print(f"  {job.id:<10} [{job.requester}]: ", end="", flush=True)
        print(token, end=" ", flush=True)
        if len(job.tokens) == job.max_tokens:
            print()

    done = asyncio.run(coord.run_all(on_token))
    print(f"  served order: {' -> '.join(j.id for j in done)}")

    _rule("Per-worker earnings (1.0 credit minted per token, split by layers)")
    total_tokens = sum(len(j.tokens) for j in done)
    for wid in sorted(coord.workers):
        share = ledger.earned_total(wid)
        print(f"  {wid:<10} earned {share:6.1f} credits")
    print(f"  {total_tokens} tokens generated -> "
          f"{sum(ledger.earned_total(w) for w in coord.workers):.1f} credits minted to workers")

    _rule("Client balances after serving (1 credit spent per token)")
    for who in ("amina", "bilal", "anon"):
        print(f"  {who:<6} balance: {ledger.balance(who):6.1f} credits")
    print("  anon paid nothing and was still served - spending buys priority, not access.")


if __name__ == "__main__":
    main()
