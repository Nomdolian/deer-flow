"""S1 — passive market making / rebate capture.

The lowest-risk starter strategy and the only one where the edge is
structural: maker fills are free and earn a rebate, so breakeven is spread
capture minus adverse selection. Everything below is adverse-selection
defence — the quoting itself is three lines.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from pmbot.config import MakerConfig
from pmbot.data.book import OrderBook
from pmbot.state import Context
from pmbot.strategies.base import BUY, SELL, BaseStrategy, Signal


class LiveOrder(Protocol):
    id: str
    token_id: str
    side: str
    price: float
    size: float
    created_ts: float


class MakerStrategy(BaseStrategy):
    name = "s1_maker"

    def __init__(
        self,
        cfg: MakerConfig,
        live_orders: Callable[[str], list[LiveOrder]],
        stale_book_max_ms: float = 1500,
    ):
        self.cfg = cfg
        self.enabled = cfg.enabled
        self.live_orders = live_orders
        self.stale_book_max_ms = stale_book_max_ms

    # ---- entry points -----------------------------------------------------

    def on_book(self, book: OrderBook, ctx: Context) -> list[Signal]:
        return self._quote(book, ctx)

    def on_tick(self, now: float, ctx: Context) -> list[Signal]:
        signals: list[Signal] = []
        for token_id in list(ctx.markets):
            book = ctx.book(token_id)
            if book is not None:
                signals.extend(self._quote(book, ctx, now=now))
        return signals

    # ---- quoting ----------------------------------------------------------

    def _quote(self, book: OrderBook, ctx: Context, now: float | None = None) -> list[Signal]:
        if not self.enabled:
            return []
        now = now if now is not None else ctx.now
        resting = self.live_orders(book.token_id)

        pull = self._pull_reason(book, ctx, now)
        if pull:
            return self._cancel_all(book.token_id, resting, pull)

        mid = book.mid
        if mid is None:
            return self._cancel_all(book.token_id, resting, "no_mid")

        position = ctx.portfolio.positions.get(book.token_id)
        inventory_usd = position.value_at(mid) if position else 0.0
        skew = self._skew(inventory_usd, book.tick_size)

        # Inventory shifts the whole quote pair, it does not widen it: long
        # inventory lowers both the bid (buy less) and the ask (sell more).
        half_spread = self.cfg.half_spread_ticks * book.tick_size
        reservation = mid - skew
        # Improve the touch by a tick to take queue priority, but never pay
        # more than fair value less our required half-spread. In a wide book
        # the touch binds and we capture most of the spread; in a tight one
        # the reservation price binds and we simply quote less aggressively.
        best_bid, best_ask = book.best_bid, book.best_ask
        bid_price = book.round_to_tick(
            min(best_bid + book.tick_size, reservation - half_spread)
            if best_bid is not None else reservation - half_spread
        )
        ask_price = book.round_to_tick(
            max(best_ask - book.tick_size, reservation + half_spread)
            if best_ask is not None else reservation + half_spread
        )

        signals: list[Signal] = []
        signals.extend(self._side_signals(book, ctx, resting, BUY, bid_price, now, mid,
                                          inventory_usd, position))
        signals.extend(self._side_signals(book, ctx, resting, SELL, ask_price, now, mid,
                                          inventory_usd, position))
        return signals

    def _side_signals(
        self,
        book: OrderBook,
        ctx: Context,
        resting: list[LiveOrder],
        side: str,
        price: float,
        now: float,
        mid: float,
        inventory_usd: float,
        position,
    ) -> list[Signal]:
        existing = [o for o in resting if o.side.upper() == side]
        size = self._size_for(book, ctx, side, price, inventory_usd, position)

        if size <= 0:
            return [self._cancel(book.token_id, o, "inventory_cap") for o in existing]

        # Do not cross the book: a "maker" order that lifts the offer pays taker fees.
        best_bid, best_ask = book.best_bid, book.best_ask
        if side == BUY and best_ask is not None and price >= best_ask:
            price = book.round_to_tick(best_ask - book.tick_size)
        if side == SELL and best_bid is not None and price <= best_bid:
            price = book.round_to_tick(best_bid + book.tick_size)
        if price <= 0 or price >= 1:
            return [self._cancel(book.token_id, o, "price_out_of_range") for o in existing]

        keep, stale = [], []
        for order in existing:
            moved = abs(order.price - price) >= self.cfg.requote_ticks * book.tick_size
            aged = (now - order.created_ts) > self.cfg.quote_max_age_s
            (stale if (moved or aged) else keep).append(order)

        signals = [self._cancel(book.token_id, o, "requote") for o in stale]
        if keep:
            return signals
        # Edge is the honest distance from our quote to fair value, not the
        # configured half-spread: in a wide book it is much larger, and in a
        # tight one it is correctly small enough to be vetoed.
        edge = (mid - price) if side == BUY else (price - mid)
        signals.append(
            Signal(
                strategy=self.name,
                token_id=book.token_id,
                side=side,
                price=price,
                size=size,
                edge=edge,
                is_maker=True,
                reason=f"quote_{side.lower()}",
            )
        )
        return signals

    def _size_for(
        self, book: OrderBook, ctx: Context, side: str, price: float,
        inventory_usd: float, position
    ) -> float:
        if price <= 0:
            return 0.0
        shares = self.cfg.quote_size_usd / price
        if side == BUY:
            headroom = self.cfg.max_inventory_usd - inventory_usd
            if headroom < self.cfg.quote_size_usd:
                return 0.0
        else:
            # Outcome tokens cannot be shorted: only offer what we hold.
            held = position.qty if position else 0.0
            shares = min(shares, held)
        if shares * price < book.min_order_size:
            return 0.0
        return round(shares, 2)

    def _skew(self, inventory_usd: float, tick: float) -> float:
        """How far to shift the quote pair against current inventory.

        Full inventory shifts both quotes by two ticks, which is enough to
        stop the book from filling us deeper into a position we cannot exit,
        and to get the position off at the same time.
        """
        if self.cfg.max_inventory_usd <= 0:
            return 0.0
        ratio = max(-1.0, min(1.0, inventory_usd / self.cfg.max_inventory_usd))
        return ratio * 2.0 * tick

    # ---- pull conditions --------------------------------------------------

    def _pull_reason(self, book: OrderBook, ctx: Context, now: float) -> str | None:
        if book.is_stale(self.stale_book_max_ms, now):
            return "stale_book"
        market = ctx.market(book.token_id)
        if market is None:
            return "unknown_market"
        minutes_left = market.hours_to_resolution(now) * 60.0
        if minutes_left < self.cfg.pull_minutes_before_resolution:
            return "near_resolution"
        baseline = ctx.baseline_volume_usd.get(book.token_id, 0.0)
        recent = ctx.recent_volume_usd.get(book.token_id, 0.0)
        if baseline > 0 and recent > baseline * self.cfg.volume_spike_multiple:
            return "volume_spike"
        return None

    def _cancel_all(self, token_id: str, resting: list[LiveOrder], reason: str) -> list[Signal]:
        return [self._cancel(token_id, order, reason) for order in resting]

    def _cancel(self, token_id: str, order: LiveOrder, reason: str) -> Signal:
        return Signal(
            strategy=self.name,
            token_id=token_id,
            side=order.side,
            price=order.price,
            size=0.0,
            is_maker=True,
            reason=f"cancel_{reason}",
            cancel_order_ids=[order.id],
        )
