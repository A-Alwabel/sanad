"""Core data types for the Sanad v0 simulation."""

from dataclasses import dataclass, field

GiB: int = 1024**3


@dataclass(frozen=True)
class ModelSpec:
    """A model described only by what the scheduler needs: layer count and size."""

    name: str
    n_layers: int
    bytes_per_layer: int

    @property
    def total_bytes(self) -> int:
        return self.n_layers * self.bytes_per_layer


@dataclass(frozen=True)
class WorkerInfo:
    """A worker's public claim: how much memory it has and which layers it hosts.

    ``layer_range`` is half-open: ``(start, end)`` hosts layers start..end-1.
    ``latency_ms`` is the per-hop latency (network RTT share + compute) this
    worker adds to the pipeline for one token.
    """

    id: str
    vram_bytes: int
    layer_range: tuple[int, int]
    latency_ms: float

    @property
    def n_layers(self) -> int:
        return self.layer_range[1] - self.layer_range[0]

    def bytes_needed(self, model: ModelSpec) -> int:
        return self.n_layers * model.bytes_per_layer


@dataclass
class Job:
    """One inference request. ``seq`` is arrival order, set by the coordinator."""

    id: str
    requester: str
    prompt: str
    max_tokens: int
    seq: int = 0
    tokens: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CreditEntry:
    """One ledger event: ``account`` earned ``amount`` = tokens * weight."""

    account: str
    tokens: int
    weight: float
    amount: float
