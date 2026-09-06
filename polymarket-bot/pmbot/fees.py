"""Fee and edge model. Build this before any strategy — it is the gate
every signal must clear, and hardcoding stale rates is how edge silently
becomes loss (rates moved three times in 2026).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

# International exchange, Sept 2026. Re-fetched at runtime by FeeTable.refresh().
DEFAULT_FEE_RATES: dict[str, float] = {
    "geopolitics": 0.0,
    "politics": 0.04,
    "finance": 0.04,
    "tech": 0.04,
    "mentions": 0.04,
    "sports": 0.05,
    "economics": 0.05,
    "culture": 0.05,
    "weather": 0.05,
    "other": 0.05,
    "crypto": 0.07,
}

# Polymarket US (CFTC-regulated) is a flat schedule with a maker rebate.
US_FEE_RATES: dict[str, float] = {"other": 0.05}

DEFAULT_CATEGORY = "other"


def taker_fee(shares: float, price: float, fee_rate: float, exponent: float = 1.0) -> float:
    """shares x feeRate x (price x (1-price))^exponent.

    The p(1-p) term is why the extremes are nearly free and why mid-priced
    taker fills are brutal. Exponent is 1 on the international exchange but
    is served by the API, so it stays a parameter.
    """
    if shares <= 0:
        return 0.0
    price = min(max(price, 0.0), 1.0)
    return shares * fee_rate * (price * (1.0 - price)) ** exponent


def fee_per_share(price: float, fee_rate: float, exponent: float = 1.0) -> float:
    return taker_fee(1.0, price, fee_rate, exponent)


def is_tradeable(
    edge_per_share: float,
    price: float,
    fee_rate: float,
    is_maker: bool,
    slippage: float = 0.005,
    exponent: float = 1.0,
) -> bool:
    """Makers pay zero and earn a rebate, so their only cost is slippage
    (really: adverse selection, which the strategy layer must handle).
    """
    cost = 0.0 if is_maker else fee_per_share(price, fee_rate, exponent)
    return edge_per_share > cost + slippage


def edge_per_day(edge_per_share: float, hours_to_resolution: float) -> float:
    """Failure mode #6: a 45-day market at 3c is worse than a 2-day market
    at 1c. Rank on edge per day held, never on raw edge.
    """
    days = max(hours_to_resolution, 1.0) / 24.0
    return edge_per_share / days


@dataclass
class FeeTable:
    """Live fee rates with drift detection.

    on_change is wired to the Telegram alerter at startup: a silent rate
    change is the difference between a profitable and an unprofitable book.
    """

    rates: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_FEE_RATES))
    exponent: float = 1.0
    updated_ts: float = field(default_factory=time.time)
    on_change: Callable[[str, float, float], None] | None = None

    def rate_for(self, category: str | None) -> float:
        if not category:
            return self.rates[DEFAULT_CATEGORY]
        return self.rates.get(category.lower(), self.rates[DEFAULT_CATEGORY])

    def rate_for_tags(self, tags: list[str] | None) -> float:
        """A market carries several tags; charge the worst one we recognise
        so a mis-tagged crypto market cannot be priced as geopolitics.
        """
        if not tags:
            return self.rates[DEFAULT_CATEGORY]
        known = [self.rates[t.lower()] for t in tags if t.lower() in self.rates]
        if not known:
            return self.rates[DEFAULT_CATEGORY]
        return max(known)

    def update(self, new_rates: dict[str, float], exponent: float | None = None) -> list[str]:
        """Apply a refreshed table; return the categories that moved."""
        changed: list[str] = []
        for category, rate in new_rates.items():
            key = category.lower()
            old = self.rates.get(key)
            if old is None or abs(old - rate) > 1e-12:
                changed.append(key)
                if self.on_change is not None:
                    self.on_change(key, old if old is not None else float("nan"), rate)
                self.rates[key] = rate
        if exponent is not None:
            self.exponent = exponent
        self.updated_ts = time.time()
        return changed
