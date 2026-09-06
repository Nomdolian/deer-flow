"""Shared mutable state: books, positions, equity, and the context object
every strategy and the risk engine read from.

One object, one owner (the orchestrator), passed by reference. Strategies
never fetch — they are given a consistent view and must not do IO.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from pmbot.data.book import OrderBook
from pmbot.data.external import SpotFeed
from pmbot.fees import FeeTable
from pmbot.universe import Market


@dataclass
class Position:
    token_id: str
    qty: float = 0.0
    avg_px: float = 0.0
    realized: float = 0.0
    updated_ts: float = field(default_factory=time.time)

    def apply_fill(self, side: str, price: float, size: float, fee: float = 0.0) -> float:
        """Update the position, return realised PnL from this fill.

        Outcome tokens are long-only in practice: a SELL closes inventory
        rather than opening a short, so realised PnL books on the sell.
        """
        realized = 0.0
        if side.upper() == "BUY":
            total = self.qty + size
            self.avg_px = ((self.avg_px * self.qty) + (price * size)) / total if total > 0 else 0.0
            self.qty = total
        else:
            closed = min(size, self.qty)
            realized = closed * (price - self.avg_px)
            self.qty = max(0.0, self.qty - size)
            if self.qty <= 1e-9:
                self.avg_px = 0.0
        realized -= fee
        self.realized += realized
        self.updated_ts = time.time()
        return realized

    def value_at(self, price: float) -> float:
        return self.qty * price

    def unrealized_at(self, price: float) -> float:
        return self.qty * (price - self.avg_px)


@dataclass
class Portfolio:
    free_usdc: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)
    daily_realized_pnl: float = 0.0
    daily_starting_equity: float = 0.0
    consecutive_losses: int = 0

    def position(self, token_id: str) -> Position:
        if token_id not in self.positions:
            self.positions[token_id] = Position(token_id=token_id)
        return self.positions[token_id]

    def position_value(self, marks: dict[str, float]) -> float:
        return sum(p.value_at(marks.get(t, p.avg_px)) for t, p in self.positions.items())

    def equity(self, marks: dict[str, float]) -> float:
        return self.free_usdc + self.position_value(marks)

    def deployed_usd(self, marks: dict[str, float]) -> float:
        return self.position_value(marks)


@dataclass
class Context:
    """Everything a strategy is allowed to see."""

    books: dict[str, OrderBook]
    markets: dict[str, Market]
    portfolio: Portfolio
    fees: FeeTable
    spot: dict[str, SpotFeed] = field(default_factory=dict)
    # Rolling per-token trade notional, used by S1's volume-spike guard.
    recent_volume_usd: dict[str, float] = field(default_factory=dict)
    baseline_volume_usd: dict[str, float] = field(default_factory=dict)
    now: float = field(default_factory=time.time)
    mode: str = "paper"

    def book(self, token_id: str) -> OrderBook | None:
        return self.books.get(token_id)

    def market(self, token_id: str) -> Market | None:
        return self.markets.get(token_id)

    def fee_rate(self, token_id: str) -> float:
        market = self.markets.get(token_id)
        return self.fees.rate_for_tags(market.tags if market else None)

    def marks(self) -> dict[str, float]:
        """Mark positions at mid where a live book exists, otherwise at cost."""
        out: dict[str, float] = {}
        for token_id, position in self.portfolio.positions.items():
            book = self.books.get(token_id)
            mid = book.mid if book else None
            out[token_id] = mid if mid is not None else position.avg_px
        return out

    def equity(self) -> float:
        return self.portfolio.equity(self.marks())

    def event_exposure_usd(self, event_id: str) -> float:
        marks = self.marks()
        total = 0.0
        for token_id, position in self.portfolio.positions.items():
            market = self.markets.get(token_id)
            if market and market.event_id == event_id:
                total += position.value_at(marks.get(token_id, position.avg_px))
        return total
