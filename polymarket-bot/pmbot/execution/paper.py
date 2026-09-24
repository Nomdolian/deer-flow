"""Paper execution: identical code path, simulated fills.

Fill rules, deliberately pessimistic:
  * A maker order fills only when the book trades *through* its price, never
    at it — you are last in queue at your own level.
  * Taker fills pay the real fee formula plus one tick of adverse slippage.
  * Maker fills pay zero fee (the rebate is not credited: don't paper-trade
    a revenue line you have not seen land).

Anything that flatters the simulation here shows up later as a live strategy
that never worked.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from pmbot.data.book import OrderBook
from pmbot.execution.registry import OrderRegistry, TrackedOrder
from pmbot.fees import taker_fee

log = logging.getLogger(__name__)


@dataclass
class SimulatedFill:
    client_id: str
    token_id: str
    side: str
    price: float
    size: float
    fee: float
    is_maker: bool
    ts: float


class PaperExecutor:
    def __init__(self, registry: OrderRegistry, fee_rate_for, fee_exponent: float = 1.0):
        self.registry = registry
        self.fee_rate_for = fee_rate_for  # Callable[[str], float]
        self.fee_exponent = fee_exponent
        self.fills: list[SimulatedFill] = []
        # Trade prints observed since each order was placed, used for through-checks.
        self._last_seen_trade: dict[str, float] = {}

    async def post(self, order: TrackedOrder, book: OrderBook) -> str:
        """Accept the order. Taker types cross immediately; makers rest."""
        exchange_id = f"paper-{order.client_id[:12]}"
        self.registry.ack(order.client_id, exchange_id)
        if order.order_type in ("FOK", "FAK"):
            self._cross(order, book)
        return exchange_id

    async def cancel(self, order_ids: list[str]) -> int:
        cancelled = 0
        for order_id in order_ids:
            if self.registry.cancel(order_id) is not None:
                cancelled += 1
        return cancelled

    async def cancel_all(self) -> int:
        return await self.cancel([o.client_id for o in self.registry.live_orders()])

    # ---- fill simulation --------------------------------------------------

    def on_book(self, book: OrderBook, now: float | None = None) -> list[SimulatedFill]:
        """Check every resting order on this token for a through-print."""
        now = now if now is not None else time.time()
        fills: list[SimulatedFill] = []
        for order in self.registry.live_orders(book.token_id):
            if order.order_type in ("FOK", "FAK"):
                continue
            if self._traded_through(order, book):
                fills.append(self._fill(order, order.price, order.remaining, is_maker=True, now=now))
        if book.last_trade_price is not None:
            self._last_seen_trade[book.token_id] = book.last_trade_price
        return fills

    def _traded_through(self, order: TrackedOrder, book: OrderBook) -> bool:
        last = book.last_trade_price
        if last is None:
            return False
        if last == self._last_seen_trade.get(book.token_id):
            return False  # no new print
        # Strictly through: a print *at* our price would have filled the queue
        # ahead of us first.
        return last < order.price if order.side.upper() == "BUY" else last > order.price

    def _cross(self, order: TrackedOrder, book: OrderBook, now: float | None = None) -> None:
        """Immediate-or-cancel against the visible book."""
        now = now if now is not None else time.time()
        side_to_take = "SELL" if order.side.upper() == "BUY" else "BUY"
        quote = book.sweep_cost(side_to_take, order.size)
        if quote is None:
            self.registry.reject(order.client_id, "no_liquidity")
            return
        avg_price, filled = quote
        if order.order_type == "FOK" and filled + 1e-9 < order.size:
            self.registry.reject(order.client_id, "fok_insufficient_size")
            return
        # One tick of adverse slippage on every taker fill.
        slipped = avg_price + book.tick_size if order.side.upper() == "BUY" else avg_price - book.tick_size
        self._fill(order, slipped, filled, is_maker=False, now=now)

    def _fill(self, order: TrackedOrder, price: float, size: float, is_maker: bool,
              now: float) -> SimulatedFill:
        fee = 0.0 if is_maker else taker_fee(
            size, price, self.fee_rate_for(order.token_id), self.fee_exponent
        )
        self.registry.fill(order.client_id, price, size, fee)
        fill = SimulatedFill(
            client_id=order.client_id, token_id=order.token_id, side=order.side,
            price=price, size=size, fee=fee, is_maker=is_maker, ts=now,
        )
        self.fills.append(fill)
        log.info("paper fill: %s %s %.2f @ %.4f (fee %.4f, maker=%s)",
                 order.side, order.token_id[:10], size, price, fee, is_maker)
        return fill
