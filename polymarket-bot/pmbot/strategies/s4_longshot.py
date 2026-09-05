"""S4 — longshot-bias fade.

Prediction markets systematically overprice tails, so the other side of a
2-6c longshot is underpriced. Fees are near zero at the extremes because of
the p(1-p) term, which is the only reason this clears costs at all.

This is picking up pennies in front of a steamroller: one resolution against
you erases many wins. It is capped hard here and again in the risk engine,
and it is off by default.
"""

from __future__ import annotations

from pmbot.config import LongshotConfig
from pmbot.data.book import OrderBook
from pmbot.fees import fee_per_share
from pmbot.risk.sizing import floor_shares
from pmbot.state import Context
from pmbot.strategies.base import BUY, BaseStrategy, Signal


class LongshotStrategy(BaseStrategy):
    name = "s4_longshot"

    def __init__(self, cfg: LongshotConfig, stale_book_max_ms: float = 1500):
        self.cfg = cfg
        self.enabled = cfg.enabled
        self.stale_book_max_ms = stale_book_max_ms

    def on_book(self, book: OrderBook, ctx: Context) -> list[Signal]:
        if not self.enabled:
            return []
        market = ctx.market(book.token_id)
        if market is None or not market.complement_token_id:
            return []
        if book.is_stale(self.stale_book_max_ms, ctx.now):
            return []

        mid = book.mid
        low, high = self.cfg.price_band
        if mid is None or not (low <= mid <= high):
            return []

        hours = market.hours_to_resolution(ctx.now)
        if hours <= 0 or hours > self.cfg.max_hours:
            return []

        # A live catalyst is exactly when the longshot is correctly priced.
        baseline = ctx.baseline_volume_usd.get(book.token_id, 0.0)
        recent = ctx.recent_volume_usd.get(book.token_id, 0.0)
        if baseline > 0 and recent > baseline * 3.0:
            return []

        favourite = ctx.book(market.complement_token_id)
        if favourite is None or favourite.is_stale(self.stale_book_max_ms, ctx.now):
            return []
        best_bid, best_ask = favourite.best_bid, favourite.best_ask
        if best_bid is None or best_ask is None:
            return []

        # Post inside the spread on the favourite rather than lifting it.
        price = favourite.round_to_tick(min(best_bid + favourite.tick_size, best_ask - favourite.tick_size))
        if price <= 0 or price >= 1:
            return []

        # Fair value of the favourite is 1 - mid(longshot); the edge is the
        # market's own inconsistency, not a forecast.
        fair = 1.0 - mid
        edge = fair - price
        fee_rate = ctx.fee_rate(market.complement_token_id)
        if edge <= fee_per_share(price, fee_rate, ctx.fees.exponent):
            return []

        # Loss if the favourite loses is the full notional, so the cap is on
        # notional, and the size is always rounded down into it.
        exposure = ctx.event_exposure_usd(market.event_id) if market.event_id else 0.0
        remaining_usd = max(0.0, self.cfg.max_loss_per_event_usd - exposure)
        shares = floor_shares(remaining_usd / price)
        if shares * price < favourite.min_order_size:
            return []

        return [
            Signal(
                strategy=self.name,
                token_id=market.complement_token_id,
                side=BUY,
                price=price,
                size=shares,
                edge=edge,
                model_p=fair,
                is_maker=True,
                reason=f"fade_longshot_{mid:.3f}",
            )
        ]
