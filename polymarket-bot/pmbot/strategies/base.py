"""Strategy protocol and the Signal every strategy emits.

A Signal is an *intent*, not an order. It carries the model's own numbers
(model_p, edge, is_maker) so the risk engine can re-derive whether the trade
clears fees rather than trusting the strategy's arithmetic.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from pmbot.data.book import OrderBook
from pmbot.state import Context

BUY = "BUY"
SELL = "SELL"

GTC = "GTC"
GTD = "GTD"
FOK = "FOK"
FAK = "FAK"


@dataclass
class Signal:
    strategy: str
    token_id: str
    side: str
    price: float
    size: float  # shares
    edge: float = 0.0  # per share, in dollars
    model_p: float | None = None
    is_maker: bool = True
    order_type: str = GTC
    expiration: int = 0
    reason: str = ""
    # Legs of one arbitrage share a group id: the executor submits them as a
    # batch and unwinds the group if any leg misses.
    group_id: str | None = None
    # Cancel intent rather than a new order (S1 requoting).
    cancel_order_ids: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ts: float = field(default_factory=time.time)

    @property
    def notional(self) -> float:
        return self.price * self.size


@runtime_checkable
class Strategy(Protocol):
    name: str
    enabled: bool

    def on_book(self, book: OrderBook, ctx: Context) -> list[Signal]:
        """Called on every book update for a watched token."""

    def on_tick(self, now: float, ctx: Context) -> list[Signal]:
        """Called on the slow loop, for time-based work (quote ageing, pulls)."""


class BaseStrategy:
    """Shared no-op implementations so a strategy only writes what it uses."""

    name = "base"
    enabled = False

    def on_book(self, book: OrderBook, ctx: Context) -> list[Signal]:
        return []

    def on_tick(self, now: float, ctx: Context) -> list[Signal]:
        return []
