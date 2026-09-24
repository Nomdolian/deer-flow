"""In-memory order book with a staleness stamp.

Every book carries last_update_ts. Nothing downstream is allowed to read a
book without asking how old it is — quoting off a dead book after a WSS gap
is failure mode #4 and it is silent until it has cost you money.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

BUY = "BUY"
SELL = "SELL"


@dataclass
class Level:
    price: float
    size: float


@dataclass
class OrderBook:
    token_id: str
    bids: dict[float, float] = field(default_factory=dict)  # price -> size
    asks: dict[float, float] = field(default_factory=dict)
    tick_size: float = 0.01
    min_order_size: float = 5.0
    last_update_ts: float = 0.0
    last_trade_price: float | None = None
    book_hash: str | None = None
    # Set when a delta arrives for a book we never snapshotted, or the hash
    # mismatches. The market feed re-snapshots and clears it.
    needs_resnapshot: bool = True

    # ---- ingest -----------------------------------------------------------

    def apply_snapshot(self, bids: list[dict], asks: list[dict], ts: float | None = None,
                       book_hash: str | None = None) -> None:
        self.bids = {float(l["price"]): float(l["size"]) for l in bids if float(l["size"]) > 0}
        self.asks = {float(l["price"]): float(l["size"]) for l in asks if float(l["size"]) > 0}
        self.book_hash = book_hash
        self.needs_resnapshot = False
        self.last_update_ts = ts if ts is not None else time.time()

    def apply_price_change(self, changes: list[dict], ts: float | None = None,
                           book_hash: str | None = None) -> None:
        """Apply a price_change delta. size == 0 removes the level.

        A delta on a book we have never snapshotted is not applied — it would
        produce a book that looks live but is missing every level we never saw.
        """
        if self.needs_resnapshot:
            return
        for change in changes:
            side = str(change.get("side", "")).upper()
            price = float(change["price"])
            size = float(change["size"])
            levels = self.bids if side in (BUY, "BID") else self.asks
            if size <= 0:
                levels.pop(price, None)
            else:
                levels[price] = size
        self.book_hash = book_hash
        self.last_update_ts = ts if ts is not None else time.time()

    def mark_stale(self) -> None:
        """Called on disconnect: the book is not to be trusted until resnapshot."""
        self.needs_resnapshot = True

    # ---- reads ------------------------------------------------------------

    @property
    def best_bid(self) -> float | None:
        return max(self.bids) if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return min(self.asks) if self.asks else None

    @property
    def mid(self) -> float | None:
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

    @property
    def spread(self) -> float | None:
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None:
            return None
        return ask - bid

    def age_ms(self, now: float | None = None) -> float:
        now = now if now is not None else time.time()
        return (now - self.last_update_ts) * 1000.0

    def is_stale(self, max_age_ms: float, now: float | None = None) -> bool:
        return self.needs_resnapshot or self.age_ms(now) > max_age_ms

    def levels(self, side: str) -> list[Level]:
        """Sorted best-first."""
        if side.upper() in (BUY, "BID"):
            return [Level(p, s) for p, s in sorted(self.bids.items(), reverse=True)]
        return [Level(p, s) for p, s in sorted(self.asks.items())]

    def depth_usd(self, side: str, within_cents: float = 2.0) -> float:
        """Real depth near touch, in USDC.

        Failure mode #3: Gamma's headline `liquidity` can be 3c deep. Size
        against this number, never against the headline.
        """
        levels = self.levels(side)
        if not levels:
            return 0.0
        touch = levels[0].price
        limit = touch - within_cents / 100.0 if side.upper() in (BUY, "BID") else touch + within_cents / 100.0
        total = 0.0
        for level in levels:
            if side.upper() in (BUY, "BID") and level.price < limit:
                break
            if side.upper() not in (BUY, "BID") and level.price > limit:
                break
            total += level.price * level.size
        return total

    def sweep_cost(self, side: str, shares: float) -> tuple[float, float] | None:
        """Cost of taking `shares` from `side` of the book.

        Returns (avg_price, filled_shares). Used to price arbitrage legs
        honestly instead of pretending the whole clip fills at touch.
        """
        remaining = shares
        notional = 0.0
        for level in self.levels(side):
            take = min(remaining, level.size)
            notional += take * level.price
            remaining -= take
            if remaining <= 1e-9:
                break
        filled = shares - remaining
        if filled <= 0:
            return None
        return notional / filled, filled

    def round_to_tick(self, price: float) -> float:
        """Orders violating tick size are rejected outright."""
        ticks = round(price / self.tick_size)
        decimals = max(0, len(f"{self.tick_size:.10f}".rstrip("0").split(".")[-1]))
        return round(ticks * self.tick_size, decimals)
