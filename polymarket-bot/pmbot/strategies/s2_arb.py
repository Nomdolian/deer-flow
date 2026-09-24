"""S2 — complementary and multi-outcome arbitrage.

The only strategy with a deterministic edge: a complete set of outcome
tokens is worth exactly $1 at resolution, so buying the set for less than
$1 (after fees) is arithmetic, not prediction.

Two shapes:
  * binary   — ask(YES) + ask(NO) < 1
  * neg-risk — sum of best asks across N mutually exclusive outcomes < 1

Both are taker trades by necessity, and both must be sized against real book
depth: the headline top-of-book size is often the whole of the opportunity.
"""

from __future__ import annotations

from collections import defaultdict

from pmbot.config import ArbConfig
from pmbot.data.book import OrderBook
from pmbot.fees import fee_per_share
from pmbot.state import Context
from pmbot.strategies.base import BUY, FOK, BaseStrategy, Signal


class ArbStrategy(BaseStrategy):
    name = "s2_arb"

    def __init__(self, cfg: ArbConfig, stale_book_max_ms: float = 1500):
        self.cfg = cfg
        self.enabled = cfg.enabled
        self.stale_book_max_ms = stale_book_max_ms

    def on_book(self, book: OrderBook, ctx: Context) -> list[Signal]:
        if not self.enabled:
            return []
        market = ctx.market(book.token_id)
        if market is None:
            return []
        if market.neg_risk and market.event_id:
            return self._neg_risk_set(market.event_id, ctx)
        if market.complement_token_id:
            return self._binary_set(book.token_id, market.complement_token_id, ctx)
        return []

    def on_tick(self, now: float, ctx: Context) -> list[Signal]:
        if not self.enabled:
            return []
        signals: list[Signal] = []
        seen_events: set[str] = set()
        seen_pairs: set[frozenset] = set()
        for token_id, market in ctx.markets.items():
            if market.neg_risk and market.event_id:
                if market.event_id in seen_events:
                    continue
                seen_events.add(market.event_id)
                signals.extend(self._neg_risk_set(market.event_id, ctx))
            elif market.complement_token_id:
                pair = frozenset({token_id, market.complement_token_id})
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                signals.extend(self._binary_set(token_id, market.complement_token_id, ctx))
        return signals

    # ---- set construction -------------------------------------------------

    def _binary_set(self, token_id: str, complement_id: str, ctx: Context) -> list[Signal]:
        return self._build([token_id, complement_id], ctx, "binary")

    def _neg_risk_set(self, event_id: str, ctx: Context) -> list[Signal]:
        by_condition: dict[str, str] = {}
        for token_id, market in ctx.markets.items():
            if market.event_id == event_id and market.neg_risk:
                # One YES leg per condition; the NO legs are not part of the set.
                by_condition.setdefault(market.condition_id, token_id)
        legs = list(by_condition.values())
        if len(legs) < 2 or len(legs) > self.cfg.max_legs:
            return []
        return self._build(legs, ctx, "neg_risk")

    def _build(self, token_ids: list[str], ctx: Context, kind: str) -> list[Signal]:
        books: list[OrderBook] = []
        for token_id in token_ids:
            book = ctx.book(token_id)
            if book is None or book.is_stale(self.stale_book_max_ms, ctx.now):
                return []
            if book.best_ask is None:
                return []
            books.append(book)

        shares = self._max_shares(books)
        if shares <= 0:
            return []

        legs: list[tuple[OrderBook, float]] = []
        cost_per_set = 0.0
        for book in books:
            quote = book.sweep_cost("SELL", shares)  # we lift the asks
            if quote is None:
                return []
            avg_price, _ = quote
            fee = fee_per_share(avg_price, ctx.fee_rate(book.token_id), ctx.fees.exponent)
            cost_per_set += avg_price + fee
            legs.append((book, avg_price))

        edge = 1.0 - cost_per_set
        if edge * 100.0 < self.cfg.min_edge_cents:
            return []

        # Cap notional: a "guaranteed" edge is only guaranteed if every leg fills.
        max_shares_by_notional = self.cfg.max_notional_usd / max(cost_per_set, 1e-6)
        shares = min(shares, max_shares_by_notional)
        shares = self._respect_min_sizes(shares, legs)
        if shares <= 0:
            return []

        group_id = f"{kind}:{'-'.join(sorted(t[:8] for t in token_ids))}:{int(ctx.now)}"
        return [
            Signal(
                strategy=self.name,
                token_id=book.token_id,
                side=BUY,
                price=book.round_to_tick(min(avg_price + book.tick_size, 0.999)),
                size=round(shares, 2),
                edge=edge / len(legs),
                is_maker=False,
                order_type=FOK,  # all-or-nothing: a partial set is naked risk
                reason=f"{kind}_set_cost_{cost_per_set:.4f}",
                group_id=group_id,
            )
            for book, avg_price in legs
        ]

    @staticmethod
    def _max_shares(books: list[OrderBook]) -> float:
        """A set is limited by its thinnest leg, counting only the top two
        levels — deeper than that and the sweep eats the edge.
        """
        per_leg: list[float] = []
        for book in books:
            levels = book.levels("SELL")[:2]
            per_leg.append(sum(level.size for level in levels))
        return min(per_leg) if per_leg else 0.0

    @staticmethod
    def _respect_min_sizes(shares: float, legs: list[tuple[OrderBook, float]]) -> float:
        for book, price in legs:
            if shares * price < book.min_order_size:
                return 0.0
        return shares


def group_by_event(markets: dict) -> dict[str, list[str]]:
    """Helper for the risk engine's correlated-event cap."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for token_id, market in markets.items():
        if market.event_id:
            grouped[market.event_id].append(token_id)
    return dict(grouped)
