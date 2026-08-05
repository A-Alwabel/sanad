"""Non-transferable credit ledger — the fairness half of Sanad.

Design (modelled on AI Horde's "kudos" system, the only volunteer inference
credit scheme still alive as of August 2026):

* Credits are minted ONLY by :meth:`CreditLedger.earn` — i.e., by doing work.
* Credits are destroyed ONLY by :meth:`CreditLedger.spend` when a requester
  is served. Spending buys queue PRIORITY, never access: balances floor at
  zero, so anonymous zero-credit users are still served (last).
* There is deliberately NO ``transfer`` method. If credits could move between
  accounts they would acquire a market price, invite speculation and Sybil
  credit-farming, and risk classification as a security or virtual currency.
  Non-transferability keeps credits a pure fairness signal — this is the
  property that kept AI Horde alive while token-incentivized networks churned.
* Anonymous use mints worker credits without burning any — deliberate,
  subsidized-access inflation, same as AI Horde's anonymous tier.
"""

from sanad.models import CreditEntry


class CreditLedger:
    """In-memory credit ledger. Persistence/auth/Sybil-resistance are future work."""

    def __init__(self) -> None:
        self._balances: dict[str, float] = {}
        self._entries: list[CreditEntry] = []

    def earn(self, account: str, tokens: int, weight: float) -> float:
        """Mint ``tokens * weight`` credits for ``account`` and return the amount.

        ``weight`` is the account's share of the work for those tokens — e.g.
        a worker serving 16 of a model's 80 layers earns weight 16/80 = 0.2
        per token, so one generated token mints exactly 1.0 credit across the
        whole pipeline.
        """
        if tokens < 0 or weight < 0:
            raise ValueError("tokens and weight must be non-negative")
        amount = tokens * weight
        self._balances[account] = self.balance(account) + amount
        self._entries.append(CreditEntry(account, tokens, weight, amount))
        return amount

    def spend(self, account: str, amount: float) -> float:
        """Burn up to ``amount`` credits; return what was actually burned.

        Clamped at zero: running out of credits demotes you to anonymous
        priority, it never blocks service.
        """
        if amount < 0:
            raise ValueError("amount must be non-negative")
        spent = min(self.balance(account), amount)
        self._balances[account] = self.balance(account) - spent
        return spent

    def balance(self, account: str) -> float:
        return self._balances.get(account, 0.0)

    def entries(self, account: str | None = None) -> list[CreditEntry]:
        if account is None:
            return list(self._entries)
        return [e for e in self._entries if e.account == account]

    def earned_total(self, account: str) -> float:
        """Lifetime earnings (for reporting; unaffected by spending)."""
        return sum(e.amount for e in self.entries(account))
