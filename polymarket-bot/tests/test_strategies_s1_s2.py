import time

import pytest

from pmbot.config import ArbConfig, MakerConfig
from pmbot.strategies.s1_maker import MakerStrategy
from pmbot.strategies.s2_arb import ArbStrategy
from tests.helpers import make_book, make_ctx, make_market


class FakeOrder:
    def __init__(self, id, side, price, created_ts, token_id="tok", size=10.0):
        self.id, self.side, self.price = id, side, price
        self.created_ts, self.token_id, self.size = created_ts, token_id, size


def maker(resting=None, **overrides):
    cfg = MakerConfig(**overrides)
    orders = resting or []
    return MakerStrategy(cfg, lambda token_id: [o for o in orders if o.token_id == token_id])


# ---- S1 -------------------------------------------------------------------

def test_quotes_improve_the_touch_in_a_wide_book():
    book = make_book(bids=((0.45, 500),), asks=((0.55, 500),))
    ctx = make_ctx(books={"tok": book}, markets={"tok": make_market()})
    ctx.portfolio.position("tok").apply_fill("BUY", 0.50, 10)  # a little inventory to offer
    signals = maker().on_book(book, ctx)
    sides = {s.side: s for s in signals}
    # One tick better than the touch, capturing most of a 10c spread.
    assert sides["BUY"].price == 0.46 and sides["SELL"].price == 0.54
    assert sides["BUY"].edge == pytest.approx(0.04)
    assert all(s.is_maker for s in signals)


def test_no_ask_without_inventory_because_tokens_cannot_be_shorted():
    book = make_book(bids=((0.45, 500),), asks=((0.55, 500),))
    ctx = make_ctx(books={"tok": book}, markets={"tok": make_market()})
    assert [s.side for s in maker().on_book(book, ctx)] == ["BUY"]


def test_inventory_skews_quotes_away_from_the_side_we_are_long():
    # A tight book, so the reservation price binds rather than the touch.
    book = make_book(bids=((0.49, 500),), asks=((0.51, 500),))
    ctx = make_ctx(books={"tok": book}, markets={"tok": make_market()})
    ctx.portfolio.position("tok").apply_fill("BUY", 0.50, 20)  # $10 long
    flat = {s.side: s.price for s in maker(max_inventory_usd=100).on_book(book, ctx)}
    ctx.portfolio.position("tok").apply_fill("BUY", 0.50, 80)  # $50 long, half the cap
    loaded = {s.side: s.price for s in maker(max_inventory_usd=100).on_book(book, ctx)}
    # Both quotes move down: buy less eagerly, sell more eagerly.
    assert loaded["BUY"] < flat["BUY"]
    assert loaded["SELL"] < flat["SELL"]


def test_buying_stops_at_the_inventory_cap():
    book = make_book(bids=((0.45, 500),), asks=((0.55, 500),))
    ctx = make_ctx(books={"tok": book}, markets={"tok": make_market()})
    ctx.portfolio.position("tok").apply_fill("BUY", 0.50, 300)  # $150 vs $100 cap
    assert not [s for s in maker(max_inventory_usd=100).on_book(book, ctx) if s.side == "BUY"]


def test_a_quote_at_the_right_price_is_left_alone():
    book = make_book(bids=((0.45, 500),), asks=((0.55, 500),))
    ctx = make_ctx(books={"tok": book}, markets={"tok": make_market()})
    resting = [FakeOrder("1", "BUY", 0.46, time.time())]  # already at the touch + a tick
    signals = maker(resting=resting).on_book(book, ctx)
    assert not [s for s in signals if s.side == "BUY"]


def test_stale_quote_is_cancelled_and_replaced():
    book = make_book(bids=((0.45, 500),), asks=((0.55, 500),))
    ctx = make_ctx(books={"tok": book}, markets={"tok": make_market()})
    old = [FakeOrder("1", "BUY", 0.49, time.time() - 120)]
    signals = maker(resting=old, quote_max_age_s=30).on_book(book, ctx)
    assert [s.cancel_order_ids for s in signals if s.cancel_order_ids] == [["1"]]
    assert any(s.side == "BUY" and not s.cancel_order_ids for s in signals)


def test_all_quotes_pulled_near_resolution():
    book = make_book(bids=((0.45, 500),), asks=((0.55, 500),))
    market = make_market(end_ts=time.time() + 300)  # 5 minutes out
    ctx = make_ctx(books={"tok": book}, markets={"tok": market})
    resting = [FakeOrder("1", "BUY", 0.49, time.time())]
    signals = maker(resting=resting, pull_minutes_before_resolution=30).on_book(book, ctx)
    assert signals and all(s.cancel_order_ids for s in signals)
    assert signals[0].reason == "cancel_near_resolution"


def test_all_quotes_pulled_on_a_volume_spike():
    book = make_book(bids=((0.45, 500),), asks=((0.55, 500),))
    ctx = make_ctx(books={"tok": book}, markets={"tok": make_market()})
    ctx.baseline_volume_usd["tok"] = 1_000
    ctx.recent_volume_usd["tok"] = 10_000  # informed flow is taking the book
    resting = [FakeOrder("1", "BUY", 0.49, time.time())]
    signals = maker(resting=resting).on_book(book, ctx)
    assert signals[0].reason == "cancel_volume_spike"


def test_stale_book_means_no_quotes():
    book = make_book(bids=((0.45, 500),), asks=((0.55, 500),), ts=time.time() - 60)
    ctx = make_ctx(books={"tok": book}, markets={"tok": make_market()})
    assert maker().on_book(book, ctx) == []


# ---- S2 -------------------------------------------------------------------

def binary_ctx(yes_ask, no_ask, size=500, tags=("geopolitics",)):
    yes = make_book(token_id="yes", bids=((yes_ask - 0.02, size),), asks=((yes_ask, size),))
    no = make_book(token_id="no", bids=((no_ask - 0.02, size),), asks=((no_ask, size),))
    markets = {
        "yes": make_market(token_id="yes", complement="no", tags=tags),
        "no": make_market(token_id="no", complement="yes", tags=tags),
    }
    return yes, make_ctx(books={"yes": yes, "no": no}, markets=markets)


def test_binary_arb_fires_when_the_set_costs_less_than_a_dollar():
    book, ctx = binary_ctx(0.45, 0.50)  # set costs 0.95, zero-fee category
    signals = ArbStrategy(ArbConfig()).on_book(book, ctx)
    assert {s.token_id for s in signals} == {"yes", "no"}
    assert all(s.order_type == "FOK" for s in signals)  # partial sets are naked risk
    assert len({s.group_id for s in signals}) == 1
    assert all(s.side == "BUY" and not s.is_maker for s in signals)


def test_no_arb_when_the_set_costs_a_dollar_or_more():
    book, ctx = binary_ctx(0.50, 0.51)
    assert ArbStrategy(ArbConfig()).on_book(book, ctx) == []


def test_fees_can_eat_a_thin_arb():
    # 1c gross on a crypto pair (0.07 both legs) does not clear the fee cost.
    book, ctx = binary_ctx(0.49, 0.50, tags=("crypto",))
    assert ArbStrategy(ArbConfig(min_edge_cents=1.5)).on_book(book, ctx) == []


def test_arb_size_is_limited_by_the_thinnest_leg():
    yes = make_book(token_id="yes", bids=((0.43, 500),), asks=((0.45, 500),))
    no = make_book(token_id="no", bids=((0.48, 500),), asks=((0.50, 12),))
    markets = {
        "yes": make_market(token_id="yes", complement="no", tags=("geopolitics",)),
        "no": make_market(token_id="no", complement="yes", tags=("geopolitics",)),
    }
    ctx = make_ctx(books={"yes": yes, "no": no}, markets=markets)
    signals = ArbStrategy(ArbConfig()).on_book(yes, ctx)
    assert signals and all(s.size <= 12 for s in signals)


def test_stale_leg_blocks_the_whole_set():
    yes, ctx = binary_ctx(0.45, 0.50)
    ctx.books["no"].mark_stale()
    assert ArbStrategy(ArbConfig()).on_book(yes, ctx) == []
