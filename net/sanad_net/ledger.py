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
import math
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

MAX_ENTRIES_IN_MEMORY = 200_000   # the file is the record; memory keeps a window


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

    _closed: bool = False

    def __post_init__(self) -> None:
        if self.path is None:
            return
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._repair_torn_tail()
        self._replay()
        self._fh = open(self.path, "a", encoding="utf-8")

    def _repair_torn_tail(self) -> None:
        """Truncate a half-written final line left by a crash.

        Without this, the next append lands on the same line and silently
        destroys the following entry too — losing a credit in exactly the crash
        the durable ledger exists to survive.
        """
        if not self.path.exists():
            return
        with open(self.path, "r+b") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            if size == 0:
                return
            fh.seek(size - 1)
            if fh.read(1) == b"\n":
                return
            fh.seek(0)
            data = fh.read()
            cut = data.rfind(b"\n")
            fh.truncate(cut + 1 if cut >= 0 else 0)

    # -- durability ----------------------------------------------------------
    @staticmethod
    def _read_file(path: Path) -> tuple[list[CreditEntry], dict[str, float]]:
        """Parse a ledger file into entries and balances. Malformed lines are
        skipped rather than fatal; non-finite numbers are rejected outright
        (the same poisoning class /register guards against)."""
        entries: list[CreditEntry] = []
        balances: dict[str, float] = {}
        if not path.exists():
            return entries, balances
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    ts, delta = float(rec["ts"]), float(rec["delta"])
                    if not (math.isfinite(ts) and math.isfinite(delta)):
                        continue
                    entry = CreditEntry(ts, str(rec["account"]), delta, str(rec.get("reason", "")))
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue
                entries.append(entry)
                balances[entry.account] = balances.get(entry.account, 0.0) + entry.delta
        return entries, balances

    def _replay(self) -> None:
        self._entries, self._balances = self._read_file(self.path)

    def _append(self, entry: CreditEntry) -> None:
        """Durably record an entry. Raises if it cannot be written — the caller
        must not apply a credit that is not on disk."""
        if self._fh is None:
            return
        self._fh.write(json.dumps({
            "ts": round(entry.ts, 3), "account": entry.account,
            "delta": round(entry.delta, 6), "reason": entry.reason,
        }, ensure_ascii=False) + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())   # a credit that is not on disk was not earned

    def _apply(self, entry: CreditEntry) -> None:
        """Write first, then apply in memory — so a failed write can never
        leave a phantom balance that the file does not back."""
        self._append(entry)
        self._balances[entry.account] = self._balances.get(entry.account, 0.0) + entry.delta
        self._entries.append(entry)
        if len(self._entries) > MAX_ENTRIES_IN_MEMORY:
            del self._entries[:len(self._entries) - MAX_ENTRIES_IN_MEMORY]

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._fh is not None:
                self._fh.close()
                self._fh = None

    # -- operations ----------------------------------------------------------
    def earn(self, account: str, amount: float, reason: str) -> None:
        if amount <= 0 or not math.isfinite(amount):
            return
        with self._lock:
            if self._closed and self.path is not None:
                raise RuntimeError("ledger is closed; refusing to mint credits it cannot record")
            self._apply(CreditEntry(time.time(), account, amount, reason))

    def spend(self, account: str, amount: float, reason: str) -> float:
        """Burn up to `amount`; clamps at zero (anonymous users are demoted,
        never blocked). Returns the amount actually burned."""
        return self.reserve(account, amount, reason)[1]

    def reserve(self, account: str, amount: float, reason: str) -> tuple[float, float]:
        """Atomically read the balance and burn up to `amount` from it.

        Returns (balance_before, burned). One lock acquisition, so two
        concurrent requests from the same account cannot both price themselves
        off the same pre-spend balance.
        """
        if not math.isfinite(amount):
            raise ValueError("amount must be finite")
        with self._lock:
            if self._closed and self.path is not None:
                raise RuntimeError("ledger is closed; refusing to record a spend")
            before = self._balances.get(account, 0.0)
            burned = min(before, max(amount, 0.0))
            if burned > 0:
                self._apply(CreditEntry(time.time(), account, -burned, reason))
            return before, burned

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
        """Re-read the ledger FILE from disk and compare it to the live balances.

        This is the check anyone holding the file can repeat, and the only
        version worth trusting: comparing two in-memory structures that are
        updated in the same breath proves nothing. An in-memory-only ledger
        reports durable=false rather than pretending to be verified.
        """
        with self._lock:
            live = dict(self._balances)
            if self.path is None:
                return {"durable": False, "consistent": None,
                        "note": "in-memory ledger; start the coordinator with --ledger to make "
                                "credits durable and independently auditable",
                        "entries_in_memory": len(self._entries), "accounts": len(live)}
            if self._fh is not None:
                self._fh.flush()
                os.fsync(self._fh.fileno())
            file_entries, file_balances = self._read_file(self.path)

        accounts = set(live) | set(file_balances)
        mismatches = {
            a: {"live": round(live.get(a, 0.0), 6), "on_disk": round(file_balances.get(a, 0.0), 6)}
            for a in accounts
            if abs(live.get(a, 0.0) - file_balances.get(a, 0.0)) > 1e-6
        }
        return {
            "durable": True,
            "path": str(self.path),
            "entries_on_disk": len(file_entries),
            "accounts": len(accounts),
            "consistent": not mismatches,
            "mismatches": mismatches,
        }
