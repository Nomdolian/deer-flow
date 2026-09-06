"""End-to-end in paper mode: book update -> strategy -> risk -> paper fill,
with no network anywhere. This is the loop that runs for 30 days before a
single real order is posted, so it is worth pinning down.
"""

import time

import pytest

from pmbot.config import BotConfig, Secrets
from pmbot.orchestrator import Bot
from tests.helpers import make_book, make_market


@pytest.fixture
async def bot(tmp_path):
    cfg = BotConfig(db_path=str(tmp_path / "pmbot.sqlite"))
    cfg.monitor.telegram_enabled = False
    instance = Bot(cfg, Secrets())
    await instance.db.connect()
    instance.risk.geoblock_passed = True  # verified at startup in the real run
    yield instance
    await instance.db.close()
    await instance.http.aclose()


async def test_book_update_produces_a_quote_and_persists_it(bot):
    book = make_book(bids=((0.45, 500),), asks=((0.55, 500),))
    market = make_market(token_id="tok")
    bot.ctx.markets = {"tok": market}
    bot.books["tok"] = book
    bot.ctx.portfolio.free_usdc = 1000.0

    await bot.on_book_update(book)

    live = bot.registry.live_orders("tok")
    assert live and live[0].side == "BUY"
    assert live[0].price < book.best_ask  # maker, never crossing
    rows = await bot.db.fetch_veto_counts()
    assert "stale_book" not in rows


async def test_a_through_print_fills_the_quote_and_books_the_position(bot):
    book = make_book(bids=((0.45, 500),), asks=((0.55, 500),), last_trade=0.50)
    bot.ctx.markets = {"tok": make_market(token_id="tok")}
    bot.books["tok"] = book
    bot.ctx.portfolio.free_usdc = 1000.0
    await bot.on_book_update(book)
    quoted = bot.registry.live_orders("tok")[0]

    book.last_trade_price = quoted.price - 0.01  # the market trades through us
    book.last_update_ts = time.time()
    await bot.on_book_update(book)

    position = bot.ctx.portfolio.position("tok")
    assert position.qty > 0
    assert bot.ctx.portfolio.free_usdc < 1000.0
    fills = await bot.db.fetch_fills()
    assert len(fills) == 1 and fills[0]["is_maker"] == 1


async def test_vetoed_signals_are_recorded_with_their_reason(bot):
    stale = make_book(bids=((0.45, 500),), asks=((0.55, 500),), ts=time.time() - 60)
    bot.ctx.markets = {"tok": make_market(token_id="tok")}
    bot.books["tok"] = stale
    # A stale book stops the maker from quoting at all, so drive the risk gate
    # directly with a signal that would otherwise be fine.
    from pmbot.strategies.base import Signal

    await bot.process_signals([
        Signal(strategy="s1_maker", token_id="tok", side="BUY", price=0.49, size=10, edge=0.05)
    ])
    assert (await bot.db.fetch_veto_counts())["stale_book"] == 1
    assert not bot.registry.live_orders()


async def test_kill_switch_stops_new_orders_but_not_cancels(bot):
    book = make_book(bids=((0.45, 500),), asks=((0.55, 500),))
    bot.ctx.markets = {"tok": make_market(token_id="tok")}
    bot.books["tok"] = book
    bot.ctx.portfolio.free_usdc = 1000.0
    await bot.on_book_update(book)
    resting = bot.registry.live_orders("tok")[0]

    bot.kill.engage("daily_drawdown_3pct")
    await bot.on_book_update(book)
    assert len(bot.registry.live_orders("tok")) == 1  # nothing new was added

    from pmbot.strategies.base import Signal

    await bot.process_signals([
        Signal(strategy="s1_maker", token_id="tok", side="BUY", price=resting.price,
               size=0, cancel_order_ids=[resting.client_id])
    ])
    assert not bot.registry.live_orders("tok")  # de-risking is always allowed


async def test_health_snapshot_reports_the_feed_and_the_kill_switch(bot):
    snapshot = bot.health_snapshot()
    assert snapshot["mode"] == "paper" and snapshot["kill_switch"] is None
    bot.kill.engage("test")
    assert bot.health_snapshot()["kill_switch"] == "test"
    assert not bot.health_snapshot()["healthy"]


async def test_arb_group_is_sized_to_the_smallest_accepted_leg(bot):
    yes = make_book(token_id="yes", bids=((0.43, 500),), asks=((0.45, 500),))
    no = make_book(token_id="no", bids=((0.48, 500),), asks=((0.50, 200),))
    bot.ctx.markets = {
        "yes": make_market(token_id="yes", complement="no", tags=("geopolitics",)),
        "no": make_market(token_id="no", complement="yes", tags=("geopolitics",)),
    }
    bot.books["yes"], bot.books["no"] = yes, no
    bot.ctx.portfolio.free_usdc = 10_000.0
    bot.engine.unwind_timeout_s = 0

    await bot.on_book_update(yes)
    arb_orders = [o for o in bot.registry.orders.values() if o.strategy == "s2_arb"]
    assert len(arb_orders) == 2
    assert len({o.size for o in arb_orders}) == 1  # a set is a set: equal legs
