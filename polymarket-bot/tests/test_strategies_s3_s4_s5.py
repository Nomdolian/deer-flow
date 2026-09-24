import time

from pmbot.config import CopyConfig, FairValueConfig, LongshotConfig
from pmbot.data.external import SpotFeed
from pmbot.strategies.s3_fairvalue import (
    FairValueStrategy,
    parse_crypto_market,
    probability_above,
)
from pmbot.strategies.s4_longshot import LongshotStrategy
from pmbot.strategies.s5_copy import CopyStrategy, WalletFill
from tests.helpers import make_book, make_ctx, make_market

# ---- S3 model -------------------------------------------------------------

def test_probability_is_a_half_at_the_money():
    assert abs(probability_above(100, 100, 0.5, 1 / 365) - 0.5) < 1e-9


def test_probability_rises_with_spot_and_falls_with_time():
    deep = probability_above(120, 100, 0.5, 1 / 365)
    assert deep > 0.99
    # More time to resolution pulls a deep-in-the-money probability back down.
    assert probability_above(120, 100, 0.5, 1.0) < deep


def test_zero_time_collapses_to_the_indicator():
    assert probability_above(101, 100, 0.5, 0) == 1.0
    assert probability_above(99, 100, 0.5, 0) == 0.0


def test_market_parsing_reads_symbol_strike_and_direction():
    spec = parse_crypto_market(make_market(question="Will Bitcoin be above $110,000 today?"))
    assert spec.symbol == "btcusdt" and spec.strike == 110_000 and spec.above


def test_no_outcome_flips_direction():
    spec = parse_crypto_market(
        make_market(question="Will Bitcoin be above $110,000 today?", outcome="No")
    )
    assert not spec.above


def test_unparseable_market_is_skipped_not_guessed():
    assert parse_crypto_market(make_market(question="Who wins the election?")) is None
    assert parse_crypto_market(make_market(question="Will Bitcoin do well?")) is None


# ---- S3 signals -----------------------------------------------------------

def s3_ctx(bid, ask, spot, minutes_left=30, samples=60):
    market = make_market(
        question="Will Bitcoin be above $100,000 today?",
        tags=("crypto",), end_ts=time.time() + minutes_left * 60,
    )
    book = make_book(bids=((bid, 500),), asks=((ask, 500),))
    ctx = make_ctx(books={"tok": book}, markets={"tok": market})
    feed = SpotFeed(symbol="btcusdt")
    base = time.time() - samples * 60
    for i in range(samples + 2):
        # A gentle wobble so realised vol is positive but small.
        feed.on_trade(spot * (1 + 0.0004 * ((-1) ** i)), ts=base + i * 60)
    feed.on_trade(spot, ts=time.time())
    ctx.spot["btcusdt"] = feed
    return book, ctx


def test_buys_when_the_model_is_far_above_the_market():
    book, ctx = s3_ctx(bid=0.30, ask=0.40, spot=105_000)  # deep ITM, market at ~0.35
    signals = FairValueStrategy(FairValueConfig(enabled=True)).on_book(book, ctx)
    assert len(signals) == 1
    signal = signals[0]
    assert signal.side == "BUY" and signal.is_maker and signal.price == 0.31
    assert signal.model_p > 0.9


def test_no_signal_when_the_market_already_agrees():
    book, ctx = s3_ctx(bid=0.49, ask=0.51, spot=100_000)  # at the money, priced at 0.50
    assert FairValueStrategy(FairValueConfig(enabled=True)).on_book(book, ctx) == []


def test_no_signal_without_enough_vol_samples():
    book, ctx = s3_ctx(bid=0.30, ask=0.40, spot=105_000, samples=5)
    assert FairValueStrategy(FairValueConfig(enabled=True, min_samples=30)).on_book(book, ctx) == []


def test_structure_filter_can_only_veto():
    book, ctx = s3_ctx(bid=0.30, ask=0.40, spot=105_000)
    strategy = FairValueStrategy(FairValueConfig(enabled=True),
                                 structure_veto=lambda symbol, side: side == "BUY")
    assert strategy.on_book(book, ctx) == []


def test_stale_spot_feed_blocks_the_model():
    book, ctx = s3_ctx(bid=0.30, ask=0.40, spot=105_000)
    ctx.spot["btcusdt"].last_update_ts = time.time() - 120
    assert FairValueStrategy(FairValueConfig(enabled=True)).on_book(book, ctx) == []


# ---- S4 -------------------------------------------------------------------

def s4_ctx(longshot_mid=0.04, hours=24):
    end_ts = time.time() + hours * 3600
    longshot = make_book(token_id="ls", bids=((longshot_mid - 0.01, 500),),
                         asks=((longshot_mid + 0.01, 500),))
    favourite = make_book(token_id="fav", bids=((0.93, 500),), asks=((0.96, 500),))
    markets = {
        "ls": make_market(token_id="ls", complement="fav", end_ts=end_ts, tags=("politics",)),
        "fav": make_market(token_id="fav", complement="ls", end_ts=end_ts, tags=("politics",)),
    }
    return longshot, make_ctx(books={"ls": longshot, "fav": favourite}, markets=markets)


def test_fades_the_longshot_by_buying_the_favourite():
    book, ctx = s4_ctx()
    signals = LongshotStrategy(LongshotConfig(enabled=True)).on_book(book, ctx)
    assert len(signals) == 1
    assert signals[0].token_id == "fav" and signals[0].side == "BUY"
    assert 0.93 < signals[0].price < 0.96


def test_ignores_prices_outside_the_band():
    longshot, ctx = s4_ctx(longshot_mid=0.20)
    assert LongshotStrategy(LongshotConfig(enabled=True)).on_book(longshot, ctx) == []


def test_ignores_markets_too_far_from_resolution():
    longshot, ctx = s4_ctx(hours=200)
    assert LongshotStrategy(LongshotConfig(enabled=True, max_hours=48)).on_book(longshot, ctx) == []


def test_a_live_catalyst_suppresses_the_fade():
    longshot, ctx = s4_ctx()
    ctx.baseline_volume_usd["ls"] = 1_000
    ctx.recent_volume_usd["ls"] = 20_000
    assert LongshotStrategy(LongshotConfig(enabled=True)).on_book(longshot, ctx) == []


def test_size_respects_the_per_event_loss_cap():
    book, ctx = s4_ctx()
    signals = LongshotStrategy(LongshotConfig(enabled=True, max_loss_per_event_usd=25)).on_book(book, ctx)
    assert signals[0].size * signals[0].price <= 25 + 1e-9


# ---- S5 -------------------------------------------------------------------

def copy_ctx():
    book = make_book(bids=((0.49, 500),), asks=((0.51, 500),))
    return make_ctx(books={"tok": book}, markets={"tok": make_market()})


def test_mirrors_a_recent_entry_at_a_maker_price():
    ctx = copy_ctx()
    strategy = CopyStrategy(CopyConfig(enabled=True, size_scalar=0.1))
    strategy.ingest([WalletFill("0xwhale", "tok", "BUY", 0.50, 1000, time.time(), "0xabc")])
    signals = strategy.on_tick(time.time(), ctx)
    assert len(signals) == 1 and signals[0].size == 100 and signals[0].is_maker


def test_skips_when_the_book_already_moved_past_their_fill():
    book = make_book(bids=((0.60, 500),), asks=((0.62, 500),))
    ctx = make_ctx(books={"tok": book}, markets={"tok": make_market()})
    strategy = CopyStrategy(CopyConfig(enabled=True, max_latency_cents=2))
    strategy.ingest([WalletFill("0xwhale", "tok", "BUY", 0.50, 1000, time.time(), "0xabc")])
    assert strategy.on_tick(time.time(), ctx) == []


def test_a_fill_is_never_mirrored_twice():
    ctx = copy_ctx()
    strategy = CopyStrategy(CopyConfig(enabled=True))
    fill = WalletFill("0xwhale", "tok", "BUY", 0.50, 1000, time.time(), "0xabc")
    strategy.ingest([fill])
    strategy.ingest([fill])
    assert len(strategy.on_tick(time.time(), ctx)) == 1


def test_exits_are_not_copied():
    ctx = copy_ctx()
    strategy = CopyStrategy(CopyConfig(enabled=True))
    strategy.ingest([WalletFill("0xwhale", "tok", "SELL", 0.50, 1000, time.time(), "0xabc")])
    assert strategy.on_tick(time.time(), ctx) == []
