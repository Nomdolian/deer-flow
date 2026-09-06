import time

from pmbot.data.book import OrderBook
from pmbot.fees import FeeTable
from pmbot.state import Context, Portfolio
from pmbot.universe import Market


def make_book(token_id="tok", bids=((0.49, 500),), asks=((0.51, 500),), ts=None,
              tick=0.01, min_size=5.0, last_trade=None):
    book = OrderBook(token_id=token_id, tick_size=tick, min_order_size=min_size)
    book.apply_snapshot(
        [{"price": p, "size": s} for p, s in bids],
        [{"price": p, "size": s} for p, s in asks],
        ts=ts if ts is not None else time.time(),
    )
    book.last_trade_price = last_trade
    return book


def make_market(token_id="tok", end_ts=None, tags=("politics",), neg_risk=False,
                complement=None, event_id="evt", liquidity=100_000.0, volume=50_000.0,
                question="Will X happen?", outcome="Yes", spread=0.02):
    return Market(
        token_id=token_id,
        condition_id="cond",
        question=question,
        outcome=outcome,
        event_id=event_id,
        tags=list(tags),
        neg_risk=neg_risk,
        tick_size=0.01,
        min_order_size=5.0,
        end_ts=end_ts if end_ts is not None else time.time() + 86_400,
        liquidity_usd=liquidity,
        volume_24h_usd=volume,
        spread=spread,
        complement_token_id=complement,
    )


def make_ctx(books=None, markets=None, free_usdc=1000.0, now_ts=None):
    books = books or {}
    markets = markets or {}
    return Context(
        books=books,
        markets=markets,
        portfolio=Portfolio(free_usdc=free_usdc, daily_starting_equity=free_usdc),
        fees=FeeTable(),
        now=now_ts if now_ts is not None else time.time(),
    )
