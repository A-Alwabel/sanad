"""Coordinator: worker registry, pipeline assembly, and the credit-priority
job queue. This is the piece Petals never had (fairness) and AI Horde never
had (sharding), joined."""

from typing import Callable, NamedTuple

from sanad.ledger import CreditLedger
from sanad.models import Job, ModelSpec
from sanad.worker import Activation, MockWorker

OnToken = Callable[[Job, str], None]


class Stage(NamedTuple):
    worker: MockWorker
    layers: tuple[int, int]  # half-open slice of the model this hop serves


class Coordinator:
    _VOCAB = (
        "the", "swarm", "carries", "what", "no", "single",
        "node", "holds", "alone", "together",
    )

    def __init__(self, model: ModelSpec, ledger: CreditLedger) -> None:
        self.model = model
        self.ledger = ledger
        self.workers: dict[str, MockWorker] = {}
        self._queue: list[Job] = []
        self._seq = 0

    # ------------------------------------------------------------- registry
    def register(self, worker: MockWorker) -> None:
        """Accept a worker only if its claimed layer range is sane and fits
        its declared memory. Over-claiming is rejected up front."""
        info = worker.info
        start, end = info.layer_range
        if not (0 <= start < end <= self.model.n_layers):
            raise ValueError(
                f"{info.id}: layer range {start}-{end} outside model "
                f"0-{self.model.n_layers}"
            )
        need = info.bytes_needed(self.model)
        if need > info.vram_bytes:
            raise ValueError(
                f"{info.id}: claims {info.n_layers} layers needing "
                f"{need / 2**30:.1f} GiB but has only "
                f"{info.vram_bytes / 2**30:.1f} GiB"
            )
        if info.id in self.workers:
            raise ValueError(f"{info.id}: already registered")
        self.workers[info.id] = worker

    # ------------------------------------------------------------- pipeline
    def build_pipeline(self) -> list[Stage]:
        """Cover layers [0, n_layers) with contiguous worker ranges,
        minimizing total per-token latency.

        Dynamic program over layer positions: from position ``pos``, any
        worker hosting ``pos`` can carry the packet to the end of its range
        (a worker entered mid-range serves the suffix, Petals-style).
        """
        n = self.model.n_layers
        inf = float("inf")
        best: list[float] = [inf] * (n + 1)
        best[0] = 0.0
        back: list[tuple[str, int] | None] = [None] * (n + 1)
        for pos in range(n):
            if best[pos] == inf:
                continue
            for w in self.workers.values():
                start, end = w.info.layer_range
                if start <= pos < end:
                    cost = best[pos] + w.info.latency_ms
                    if cost < best[end]:
                        best[end] = cost
                        back[end] = (w.info.id, pos)
        if best[n] == inf:
            raise RuntimeError(
                f"registered workers cannot cover all {n} layers of "
                f"{self.model.name}"
            )
        stages: list[Stage] = []
        pos = n
        while pos > 0:
            assert back[pos] is not None
            worker_id, prev = back[pos]
            stages.append(Stage(self.workers[worker_id], (prev, pos)))
            pos = prev
        stages.reverse()
        return stages

    def pipeline_latency_ms(self, stages: list[Stage]) -> float:
        return sum(s.worker.info.latency_ms for s in stages)

    # ---------------------------------------------------------------- queue
    def submit(self, job: Job) -> None:
        job.seq = self._seq
        self._seq += 1
        self._queue.append(job)

    def queue_order(self) -> list[Job]:
        """Jobs by requester credit balance (desc), arrival order as tiebreak.

        Zero-balance (anonymous) jobs sort last but are never dropped: the
        dispatcher drains the whole queue, so every job is eventually served.
        """
        return sorted(
            self._queue,
            key=lambda j: (-self.ledger.balance(j.requester), j.seq),
        )

    # ------------------------------------------------------------- dispatch
    async def run_next(self, on_token: OnToken | None = None) -> Job:
        """Serve the highest-priority queued job to completion."""
        if not self._queue:
            raise IndexError("job queue is empty")
        job = self.queue_order()[0]
        self._queue.remove(job)
        stages = self.build_pipeline()
        for step in range(job.max_tokens):
            packet = Activation(job_id=job.id, step=step)
            for stage in stages:
                packet = await stage.worker.process(packet, stage.layers, self.model)
            token = self._decode(job, step)
            job.tokens.append(token)
            # Each generated token costs 1 credit — exactly what the pipeline
            # workers collectively earned for it (clamped at 0 for anonymous).
            self.ledger.spend(job.requester, 1.0)
            if on_token is not None:
                on_token(job, token)
        return job

    async def run_all(self, on_token: OnToken | None = None) -> list[Job]:
        """Drain the queue in priority order; return jobs in served order."""
        done: list[Job] = []
        while self._queue:
            done.append(await self.run_next(on_token))
        return done

    def _decode(self, job: Job, step: int) -> str:
        """Deterministic mock 'lm_head': pick a word from a tiny vocabulary."""
        seed = sum(f"{job.id}:{job.prompt}".encode())
        return self._VOCAB[(seed + step) % len(self._VOCAB)]
