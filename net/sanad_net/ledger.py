"""Non-transferable credit ledger (real-network variant).

Core rules per GOVERNANCE.md: credits are minted only by serving verified
tokens, spent only as queue priority, and there is no transfer path —
deliberately, so credits can never become a tradeable asset.

Note: semantics here have intentionally diverged from the prototype/
simulation — net/ escrows a job's expected cost at submit and settles after
the run (refunds included), while the simulation spends at completion. net/
is authoritative; the simulation illustrates concepts.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class CreditEntry:
    ts: float
    account: str
    delta: float
    reason: str


@dataclass
class Ledger:
    _balances: dict[str, float] = field(default_factory=dict)
    _entries: list[CreditEntry] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def earn(self, account: str, amount: float, reason: str) -> None:
        if amount <= 0:
            return
        with self._lock:
            self._balances[account] = self._balances.get(account, 0.0) + amount
            self._entries.append(CreditEntry(time.time(), account, amount, reason))

    def spend(self, account: str, amount: float, reason: str) -> float:
        """Burn up to `amount`; clamps at zero (anonymous users are demoted, never blocked)."""
        with self._lock:
            bal = self._balances.get(account, 0.0)
            spent = min(bal, max(amount, 0.0))
            if spent > 0:
                self._balances[account] = bal - spent
                self._entries.append(CreditEntry(time.time(), account, -spent, reason))
            return spent

    def balance(self, account: str) -> float:
        with self._lock:
            return self._balances.get(account, 0.0)

    def balances(self) -> dict[str, float]:
        with self._lock:
            return dict(self._balances)

    def entries(self) -> list[CreditEntry]:
        with self._lock:
            return list(self._entries)
