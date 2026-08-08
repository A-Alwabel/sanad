"""Non-transferable credit ledger — append-only and durable.

Core rules per GOVERNANCE.md: credits are minted only by serving verified
tokens, spent only as queue priority, and there is no transfer path —
deliberately, so credits can never become a tradeable asset.

Durability matters as much as the rules: GOVERNANCE.md promises that
withdrawal is never punished and that contributions are kept. A ledger that
evaporates when the coordinator restarts breaks that promise, so every entry
is appended to a JSONL file as it happens and replayed on startup. The file is
the ledger; the in-memory balances are a cache of it. That also makes the
"append-only, auditable" claim in the docs literally true: anyone can recompute
every balance from the file.

Note: semantics here have intentionally diverged from the prototype/
simulation — net/ escrows a job's expected cost at submit and settles after
the run (refunds included), while the simulation spends at completion. net/
is authoritative; the simulation illustrates concepts.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CreditEntry:
    ts: float
    account: str
    delta: float
    reason: str


@dataclass
class Ledger:
    """Thread-safe, append-only, optionally file-backed."""

    path: Path | None = None
    _balances: dict[str, float] = field(default_factory=dict)
    _entries: list[CreditEntry] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _fh: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.path is None:
            return
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._replay()
        self._fh = open(self.path, "a", encoding="utf-8")

    # -- durability ----------------------------------------------------------
    def _replay(self) -> None:
        """Rebuild balances from the file. Malformed trailing lines (a crash
        mid-write) are skipped rather than fatal."""
        if not self.path.exists():
            return
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    entry = CreditEntry(float(rec["ts"]), str(rec["account"]),
                                        float(rec["delta"]), str(rec.get("reason", "")))
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue
                self._entries.append(entry)
                self._balances[entry.account] = self._balances.get(entry.account, 0.0) + entry.delta

    def _append(self, entry: CreditEntry) -> None:
        if self._fh is None:
            return
        self._fh.write(json.dumps({
            "ts": round(entry.ts, 3), "account": entry.account,
            "delta": round(entry.delta, 6), "reason": entry.reason,
        }, ensure_ascii=False) + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())   # a credit that is not on disk was not earned

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None

    # -- operations ----------------------------------------------------------
    def earn(self, account: str, amount: float, reason: str) -> None:
        if amount <= 0:
            return
        with self._lock:
            entry = CreditEntry(time.time(), account, amount, reason)
            self._balances[account] = self._balances.get(account, 0.0) + amount
            self._entries.append(entry)
            self._append(entry)

    def spend(self, account: str, amount: float, reason: str) -> float:
        """Burn up to `amount`; clamps at zero (anonymous users are demoted,
        never blocked). Returns the amount actually burned."""
        with self._lock:
            bal = self._balances.get(account, 0.0)
            spent = min(bal, max(amount, 0.0))
            if spent > 0:
                entry = CreditEntry(time.time(), account, -spent, reason)
                self._balances[account] = bal - spent
                self._entries.append(entry)
                self._append(entry)
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

    def audit(self) -> dict:
        """Recompute balances from the entry log — the check anyone can repeat
        against the published file."""
        with self._lock:
            recomputed: dict[str, float] = {}
            for e in self._entries:
                recomputed[e.account] = recomputed.get(e.account, 0.0) + e.delta
            mismatches = {
                a: {"cached": round(self._balances.get(a, 0.0), 6), "replayed": round(v, 6)}
                for a, v in recomputed.items()
                if abs(self._balances.get(a, 0.0) - v) > 1e-6
            }
            return {"entries": len(self._entries), "accounts": len(recomputed),
                    "consistent": not mismatches, "mismatches": mismatches}
