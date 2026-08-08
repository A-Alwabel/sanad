"""Which models actually serve your community well — measured, not guessed.

Download counts are a popularity signal with well-known biases; they tell you a
model is famous, not that it answers *your* people well. This records a simple
signal — was this answer good? — per model, so the ladder can be grown toward
what performs here rather than what trends on a hub.

Design borrowed from the ledger, and from the hard lessons in
[docs/rfc/0001-human-loop.md](../../docs/rfc/0001-human-loop.md):

- **Append-only and durable.** Every rating is a line on disk with the model,
  the verdict, and a pseudonymous rater id, so it survives restarts and can be
  audited or filtered after the fact — the same defence AI Horde landed on when
  its ratings were botted within days.
- **Small-N honest.** A model on two upvotes does not outrank one on two
  hundred. The score is the Wilson lower bound of the up-rate, which stays low
  until there is enough evidence to trust it.
- **Advisory only.** This never switches the served model on its own. It ranks
  a *suggestion* a human acts on — because letting a popularity or rating
  number auto-deploy a model is exactly the poisoning channel the RFC refuses
  to open.

This is v0 data collection: it is gameable (anyone who can reach the coordinator
can rate), and it is honest about that. Fraud resistance is RFC 0001's job.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


def wilson_lower_bound(up: int, total: int, z: float = 1.96) -> float:
    """Lower bound of the 95% confidence interval for the up-rate.

    Rewards evidence, not luck: 2/2 scores far below 190/200. This is the same
    ranking Reddit uses for 'best' comments, and it is the right shape here —
    a model earns a high rank by being liked *often enough to be sure*.
    """
    if total == 0:
        return 0.0
    p = up / total
    denom = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max((centre - margin) / denom, 0.0)


@dataclass
class FeedbackStore:
    path: Path | None = None
    _tally: dict[str, dict[str, int]] = field(default_factory=dict)   # model -> {up,down}
    _entries: int = 0
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _fh: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.path is None:
            return
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._replay()
        self._fh = open(self.path, "a", encoding="utf-8")

    def _replay(self) -> None:
        if not self.path.exists():
            return
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    self._apply_mem(str(rec["model"]), str(rec["verdict"]))
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue

    def _apply_mem(self, model: str, verdict: str) -> None:
        t = self._tally.setdefault(model, {"up": 0, "down": 0})
        if verdict == "up":
            t["up"] += 1
        elif verdict == "down":
            t["down"] += 1
        else:
            return
        self._entries += 1

    def rate(self, model: str, verdict: str, rater: str = "anon",
             prompt_hash: str = "") -> None:
        """Record one rating. verdict is 'up' or 'down'."""
        if verdict not in ("up", "down") or not model:
            raise ValueError("verdict must be 'up' or 'down' and model must be set")
        with self._lock:
            if self._fh is not None:
                self._fh.write(json.dumps({
                    "ts": round(time.time(), 3), "model": model, "verdict": verdict,
                    "rater": rater, "prompt_hash": prompt_hash,
                }, ensure_ascii=False) + "\n")
                self._fh.flush()
                os.fsync(self._fh.fileno())
            self._apply_mem(model, verdict)

    def score(self, model: str) -> float:
        with self._lock:
            t = self._tally.get(model)
            if not t:
                return 0.0
            return wilson_lower_bound(t["up"], t["up"] + t["down"])

    def ranking(self) -> list[dict]:
        """Per-model standing, best first. The number the Scout should trust."""
        with self._lock:
            rows = []
            for model, t in self._tally.items():
                total = t["up"] + t["down"]
                rows.append({
                    "model": model, "up": t["up"], "down": t["down"], "total": total,
                    "score": round(wilson_lower_bound(t["up"], total), 4),
                    "approval": round(t["up"] / total, 3) if total else None,
                })
            rows.sort(key=lambda r: r["score"], reverse=True)
            return rows

    def total_ratings(self) -> int:
        with self._lock:
            return self._entries

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None
