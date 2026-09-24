import time

from pmbot.config import RiskConfig
from pmbot.risk.engine import RiskEngine
from pmbot.risk.killswitch import KillSwitch
from pmbot.risk.sizing import cap_by_depth, kelly_shares, round_to_min_size
from pmbot.strategies.base import Signal
from tests.helpers import make_book, make_ctx, make_market


def engine(**overrides):
    cfg = RiskConfig(require_geoblock_pass=False, **overrides)
    return RiskEngine(cfg, KillSwitch(), geoblock_passed=True)


def signal(**overrides):
    defaults = {"strategy": "s1_maker", "token_id": "tok", "side": "BUY", "price": 0.50,
                "size": 100.0, "edge": 0.05, "is_maker": True}
    defaults.update(overrides)
    return Signal(**defaults)


def ctx_with(**overrides):
    book = overrides.pop("book", None) or make_book()
    market = overrides.pop("market", None) or make_market()
    return make_ctx(books={"tok": book}, markets={"tok": market}, **overrides)


# ---- gates ----------------------------------------------------------------

def test_kill_switch_blocks_everything():
    risk = engine()
    risk.kill.engage("daily_drawdown")
    decision = risk.evaluate(signal(), ctx_with())
    assert not decision.accepted and decision.reason.startswith("kill_switch")


def test_cancels_are_never_vetoed_even_while_halted():
    risk = engine()
    risk.kill.engage("daily_drawdown")
    decision = risk.evaluate(signal(cancel_order_ids=["abc"]), ctx_with())
    assert decision.accepted and decision.reason == "cancel"


def test_geoblock_must_be_verified_before_any_order():
    risk = RiskEngine(RiskConfig(), KillSwitch(), geoblock_passed=False)
    assert risk.evaluate(signal(), ctx_with()).reason == "geoblock_not_verified"


def test_stale_book_is_vetoed():
    book = make_book(ts=time.time() - 10)
    assert engine().evaluate(signal(), ctx_with(book=book)).reason == "stale_book"


def test_off_tick_price_is_vetoed_before_the_exchange_rejects_it():
    assert engine().evaluate(signal(price=0.4732), ctx_with()).reason == "price_off_tick"


def test_edge_below_the_fee_floor_is_vetoed():
    decision = engine().evaluate(signal(edge=0.001), ctx_with())
    assert decision.reason.startswith("edge_below_fee_floor")


def test_taker_fee_is_charged_against_the_edge():
    # 2c of edge survives as a maker and dies as a taker at p=0.5, rate 0.04:
    # the taker pays 0.04 x 0.5 x 0.5 = 1c, leaving less than the 1.5c floor.
    ctx = ctx_with(market=make_market(tags=("politics",)))
    predictive = {"strategy": "s3_fairvalue", "edge": 0.02}
    assert engine().evaluate(signal(is_maker=True, **predictive), ctx).accepted
    assert not engine().evaluate(signal(is_maker=False, **predictive), ctx).accepted


def test_makers_get_their_own_edge_floor():
    # Spread capture at zero fee is the maker's business: 0.6c is a trade for
    # S1 and not for a predictive strategy paying the taker floor.
    ctx = ctx_with()
    assert engine().evaluate(signal(strategy="s1_maker", edge=0.006), ctx).accepted
    assert not engine().evaluate(signal(strategy="s3_fairvalue", edge=0.006), ctx).accepted


def test_long_dated_market_fails_the_edge_per_day_gate():
    market = make_market(end_ts=time.time() + 200 * 86400)
    decision = engine(min_edge_per_day_cents=0.5).evaluate(signal(edge=0.03), ctx_with(market=market))
    assert decision.reason == "edge_per_day_too_low"


def test_disputed_market_is_vetoed():
    market = make_market()
    market.disputed = True
    assert engine().evaluate(signal(), ctx_with(market=market)).reason == "resolution_disputed"


# ---- sizing ---------------------------------------------------------------

def test_size_is_capped_by_position_pct_of_equity():
    risk = engine(max_position_pct_per_market=2)
    ctx = ctx_with(free_usdc=1000.0)
    decision = risk.evaluate(signal(size=10_000), ctx)
    # 2% of $1000 equity = $20 notional = 40 shares at 0.50.
    assert decision.accepted and decision.size <= 40


def test_size_is_capped_by_visible_depth_not_headline_liquidity():
    thin = make_book(asks=((0.51, 8),))
    decision = engine().evaluate(signal(size=1000), ctx_with(book=thin, free_usdc=100_000))
    assert decision.size <= 8 * 0.25 + 1e-9


def test_event_exposure_cap_binds_across_correlated_markets():
    risk = engine(max_exposure_pct_per_event=5, max_position_pct_per_market=50)
    ctx = ctx_with(free_usdc=1000.0)
    ctx.portfolio.position("other").apply_fill("BUY", 0.50, 200)  # $100 in the same event
    ctx.markets["other"] = make_market(token_id="other", event_id="evt")
    ctx.books["other"] = make_book(token_id="other")
    decision = risk.evaluate(signal(size=1000), ctx)
    assert not decision.accepted and decision.reason == "event_exposure_cap"


def test_cannot_sell_more_than_held():
    ctx = ctx_with()
    ctx.portfolio.position("tok").apply_fill("BUY", 0.40, 30)
    decision = engine().evaluate(signal(side="SELL", size=100, edge=0.05), ctx)
    assert decision.accepted and decision.size == 30


def test_selling_with_no_inventory_is_vetoed():
    decision = engine().evaluate(signal(side="SELL", size=100), ctx_with())
    assert not decision.accepted and decision.reason == "no_inventory_to_sell"


def test_max_open_orders_stops_new_buys():
    risk = engine(max_open_orders=1)
    risk.open_order_count = 1
    assert risk.evaluate(signal(), ctx_with()).reason == "max_open_orders"


def test_below_min_order_size_sizes_to_zero_not_to_the_minimum():
    book = make_book(min_size=5.0)
    decision = engine().evaluate(signal(size=1.0, edge=0.02), ctx_with(book=book, free_usdc=20))
    assert not decision.accepted


# ---- kelly ----------------------------------------------------------------

def test_kelly_scales_with_edge_and_is_capped():
    small = kelly_shares(0.02, 0.5, 1000)
    large = kelly_shares(0.20, 0.5, 1000)
    assert large > small
    # cap_pct=2% of $1000 = $20 = 40 shares at 0.50
    assert large * 0.5 <= 1000 * 0.02 + 1e-9


def test_kelly_refuses_negative_edge():
    assert kelly_shares(-0.05, 0.5, 1000) == 0.0


def test_kelly_scales_with_live_equity():
    assert kelly_shares(0.05, 0.5, 2000) == 2 * kelly_shares(0.05, 0.5, 1000)


def test_min_size_rounds_to_zero():
    assert round_to_min_size(4, 0.5, min_order_size=5.0) == 0.0
    assert round_to_min_size(40, 0.5, min_order_size=5.0) == 40


def test_depth_cap_uses_the_opposite_side():
    book = make_book(asks=((0.51, 40),))
    assert cap_by_depth(1000, book, "BUY") == 10


def test_maker_size_is_the_configured_clip_not_a_kelly_number():
    # A 0.6c maker edge would size to ~zero under Kelly; the quote is a clip.
    ctx = ctx_with(free_usdc=1000.0)
    decision = engine().evaluate(signal(strategy="s1_maker", edge=0.006, size=40), ctx)
    assert decision.accepted and decision.size == 40


def test_predictive_strategies_are_still_kelly_sized():
    ctx = ctx_with(free_usdc=1000.0)
    thin = engine().evaluate(signal(strategy="s3_fairvalue", edge=0.02, size=1000), ctx)
    fat = engine().evaluate(signal(strategy="s3_fairvalue", edge=0.20, size=1000), ctx)
    assert thin.size < fat.size


def deep_ctx():
    # Deep book and a loose per-market cap, so the strategy cap is the
    # binding constraint rather than depth or position size.
    return ctx_with(book=make_book(asks=((0.51, 100_000),), bids=((0.49, 100_000),)),
                    free_usdc=100_000.0)


def test_journal_weights_scale_a_strategys_cap():
    ctx = deep_ctx()
    hot = engine(max_position_pct_per_market=100)
    cold = engine(max_position_pct_per_market=100)
    hot.strategy_weights = {"s1_maker": 1.5}
    cold.strategy_weights = {"s1_maker": 0.1}
    big = hot.evaluate(signal(strategy="s1_maker", size=1_000_000, edge=0.02), ctx)
    small = cold.evaluate(signal(strategy="s1_maker", size=1_000_000, edge=0.02), ctx)
    assert big.size > small.size


def test_adaptive_weight_is_capped():
    ctx = deep_ctx()
    capped = engine(max_position_pct_per_market=100)
    absurd = engine(max_position_pct_per_market=100)
    capped.strategy_weights = {"s1_maker": 1.5}
    absurd.strategy_weights = {"s1_maker": 50.0}
    args = {"strategy": "s1_maker", "size": 1_000_000, "edge": 0.02}
    assert capped.evaluate(signal(**args), ctx).size == absurd.evaluate(signal(**args), ctx).size
