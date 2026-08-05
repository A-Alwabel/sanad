"""Mock pipeline worker: simulates per-hop latency, forwards activation
packets, and earns credits per token proportional to layers served."""

import asyncio
from dataclasses import dataclass, field

from sanad.ledger import CreditLedger
from sanad.models import ModelSpec, WorkerInfo


@dataclass
class Activation:
    """Stand-in for the hidden-state tensor passed between pipeline stages.

    In a real deployment this is ~2-30 KB per token per hop (the reason
    pipeline sharding is WAN-viable at all); here it is bookkeeping only.
    """

    job_id: str
    step: int
    layers_done: int = 0
    visited: list[str] = field(default_factory=list)


class MockWorker:
    """Hosts ``info.layer_range`` of a model. ``time_scale`` scales the
    simulated latency sleep (0.0 = don't sleep, used by tests)."""

    def __init__(self, info: WorkerInfo, ledger: CreditLedger, time_scale: float = 0.0) -> None:
        self.info = info
        self.ledger = ledger
        self.time_scale = time_scale

    async def process(
        self, packet: Activation, layers: tuple[int, int], model: ModelSpec
    ) -> Activation:
        """Mock one pipeline hop: 'compute' layers [a, b) and forward."""
        a, b = layers
        start, end = self.info.layer_range
        if not (start <= a < b <= end):
            raise ValueError(
                f"worker {self.info.id} asked for layers {a}-{b} "
                f"but hosts only {start}-{end}"
            )
        if self.time_scale > 0:
            await asyncio.sleep(self.info.latency_ms * self.time_scale / 1000.0)
        else:
            await asyncio.sleep(0)  # still yield to the event loop
        packet.layers_done += b - a
        packet.visited.append(self.info.id)
        # One token forwarded: earn this worker's layer-share of 1.0 credit.
        self.ledger.earn(self.info.id, tokens=1, weight=(b - a) / model.n_layers)
        return packet
