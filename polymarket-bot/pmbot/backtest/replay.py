"""Replay /prices-history through the live strategy code.

Read the caveat before you trust a number out of this file: prices-history
is mid/last-trade only. There is no book depth and no queue, so the synthetic
book below is an assumption, and maker strategies will look better here than
they are. Use replay to catch obviously broken logic. Paper trading against
the real book is the actual test.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pmbot.data.book import OrderBook
from pmbot.state import Context, Portfolio
from pmbot.strategies.base import BUY, Signal
from pmbot.universe import Market


@dataclass
class ReplayFill:
    ts: int
    token_id: str
    strategy: str
    side: str
    price: float
    size: float
    pnl: float = 0.0


@dataclass
class ReplayResult:
    fills: list[ReplayFill] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    signals: int = 0

    @property
    def pnls(self) -> list[float]:
        return [f.pnl for f in self.fills if f.pnl != 0.0]


def synthetic_book(token_id: str, mid: float, ts: float, assumed_spread: float = 0.02,
                   depth_shares: float = 500.0, tick_size: float = 0.01) -> OrderBook:
    """A two-sided book around a historical mid.

    The spread and depth are assumptions, not data. Widen assumed_spread if
    you want the replay to be less flattering — that is the honest direction
    to be wrong in.
    """
    book = OrderBook(token_id=token_id, tick_size=tick_size)
    half = assumed_spread / 2.0
    bid = max(tick_size, round((mid - half) / tick_size) * tick_size)
    ask = min(1 - tick_size, round((mid + half) / tick_size) * tick_size)
    book.apply_snapshot(
        [{"price": bid, "size": depth_shares}, {"price": bid - tick_size, "size": depth_shares}],
        [{"price": ask, "size": depth_shares}, {"price": ask + tick_size, "size": depth_shares}],
        ts=ts,
    )
    book.last_trade_price = mid
    return book


class ReplayEngine:
    """Runs one strategy over one token's history."""

    def __init__(self, strategy, market: Market, fees, starting_equity: float = 1000.0,
                 assumed_spread: float = 0.02):
        self.strategy = strategy
        self.market = market
        self.fees = fees
        self.starting_equity = starting_equity
        self.assumed_spread = assumed_spread

    def run(self, series: list[tuple[int, float]]) -> ReplayResult:
        portfolio = Portfolio(free_usdc=self.starting_equity)
        ctx = Context(books={}, markets={self.market.token_id: self.market},
                      portfolio=portfolio, fees=self.fees)
        result = ReplayResult()
        resting: list[Signal] = []

        for ts, mid in series:
            book = synthetic_book(self.market.token_id, mid, ts, self.assumed_spread,
                                  tick_size=self.market.tick_size)
            ctx.books[self.market.token_id] = book
            ctx.now = float(ts)

            # Resolve resting quotes first: a maker order fills only when the
            # print goes through its price.
            still_resting: list[Signal] = []
            for signal in resting:
                through = mid < signal.price if signal.side == BUY else mid > signal.price
                if through:
                    result.fills.append(self._apply(signal, ts, portfolio, mid))
                else:
                    still_resting.append(signal)
            resting = still_resting

            new_signals = [s for s in self.strategy.on_book(book, ctx) if not s.cancel_order_ids]
            result.signals += len(new_signals)
            resting.extend(new_signals)
            result.equity_curve.append(portfolio.equity({self.market.token_id: mid}))
        return result

    def _apply(self, signal: Signal, ts: int, portfolio: Portfolio, mark: float) -> ReplayFill:
        position = portfolio.position(signal.token_id)
        pnl = position.apply_fill(signal.side, signal.price, signal.size, fee=0.0)
        if signal.side == BUY:
            portfolio.free_usdc -= signal.price * signal.size
        else:
            portfolio.free_usdc += signal.price * signal.size
        return ReplayFill(
            ts=ts, token_id=signal.token_id, strategy=signal.strategy, side=signal.side,
            price=signal.price, size=signal.size, pnl=pnl,
        )
