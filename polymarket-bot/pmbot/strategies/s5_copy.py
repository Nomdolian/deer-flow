"""S5 — smart-money copy trading.

The Data API exposes any wallet's fills, so mirroring is cheap to build. It
is good for *market selection* and weak as a standalone edge: by the time a
fill is public you are behind it, which is what the latency filter enforces.

Strategies do no IO. The orchestrator polls the Data API and feeds fills in
through ingest().
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from pmbot.config import CopyConfig
from pmbot.state import Context
from pmbot.strategies.base import BUY, BaseStrategy, Signal


@dataclass
class WalletFill:
    wallet: str
    token_id: str
    side: str
    price: float
    size: float
    ts: float
    tx_hash: str = ""

    @property
    def key(self) -> str:
        return self.tx_hash or f"{self.wallet}:{self.token_id}:{self.ts}:{self.price}"


class CopyStrategy(BaseStrategy):
    name = "s5_copy"

    def __init__(self, cfg: CopyConfig, stale_book_max_ms: float = 1500, max_age_s: float = 300.0):
        self.cfg = cfg
        self.enabled = cfg.enabled
        self.stale_book_max_ms = stale_book_max_ms
        self.max_age_s = max_age_s
        self._pending: list[WalletFill] = []
        self._seen: set[str] = set()

    def ingest(self, fills: list[WalletFill]) -> None:
        for fill in fills:
            if fill.key in self._seen:
                continue
            self._seen.add(fill.key)
            self._pending.append(fill)
        if len(self._seen) > 50_000:
            self._seen = set(list(self._seen)[-10_000:])

    def on_tick(self, now: float, ctx: Context) -> list[Signal]:
        if not self.enabled or not self._pending:
            return []
        now = now or time.time()
        signals: list[Signal] = []
        pending, self._pending = self._pending, []
        for fill in pending:
            signal = self._mirror(fill, ctx, now)
            if signal is not None:
                signals.append(signal)
        return signals

    def _mirror(self, fill: WalletFill, ctx: Context, now: float) -> Signal | None:
        if fill.side.upper() != BUY:
            return None  # exits are theirs to time, not ours to chase
        if now - fill.ts > self.max_age_s:
            return None
        book = ctx.book(fill.token_id)
        if book is None or book.is_stale(self.stale_book_max_ms, now):
            return None
        best_bid, best_ask = book.best_bid, book.best_ask
        if best_bid is None or best_ask is None:
            return None

        # Latency filter: if the book already moved through their fill, the
        # edge they had is the edge we would be paying for.
        if (best_ask - fill.price) * 100.0 > self.cfg.max_latency_cents:
            return None

        price = book.round_to_tick(min(best_bid + book.tick_size, best_ask - book.tick_size))
        size = fill.size * self.cfg.size_scalar
        if price <= 0 or price >= 1 or size * price < book.min_order_size:
            return None
        return Signal(
            strategy=self.name,
            token_id=fill.token_id,
            side=BUY,
            price=price,
            size=round(size, 2),
            edge=max(0.0, fill.price - price),
            is_maker=True,
            reason=f"copy_{fill.wallet[:10]}",
        )
