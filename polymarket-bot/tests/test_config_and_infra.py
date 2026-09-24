import json
import time

from pmbot.backtest.replay import ReplayEngine, synthetic_book
from pmbot.config import load_config
from pmbot.data.external import BinanceFeed, SpotFeed
from pmbot.data.gamma import parse_market
from pmbot.data.ratelimit import TokenBucket
from pmbot.fees import FeeTable
from pmbot.risk.killswitch import KillSwitch
from tests.helpers import make_market

# ---- config ---------------------------------------------------------------

def test_defaults_are_the_starter_config(tmp_path):
    cfg = load_config(tmp_path / "missing.yaml")
    assert cfg.mode == "paper"  # never live by accident
    assert cfg.strategies.s1_maker.enabled and cfg.strategies.s2_arb.enabled
    assert not cfg.strategies.s3_fairvalue.enabled  # the predictive ones start off


def test_yaml_overrides_defaults(tmp_path):
    path = tmp_path / "bot.yaml"
    path.write_text("mode: paper\nrisk:\n  kelly_fraction: 0.1\nuniverse:\n  max_markets: 5\n")
    cfg = load_config(path)
    assert cfg.risk.kelly_fraction == 0.1 and cfg.universe.max_markets == 5


def test_env_overrides_yaml(tmp_path, monkeypatch):
    path = tmp_path / "bot.yaml"
    path.write_text("mode: paper\nrisk:\n  kelly_fraction: 0.25\n")
    monkeypatch.setenv("PMBOT_RISK__KELLY_FRACTION", "0.05")
    monkeypatch.setenv("PMBOT_UNIVERSE__MAX_MARKETS", "3")
    cfg = load_config(path)
    assert cfg.risk.kelly_fraction == 0.05 and cfg.universe.max_markets == 3


def test_real_bot_yaml_parses():
    cfg = load_config("bot.yaml")
    assert cfg.mode == "paper" and cfg.signature_type in (0, 1, 2)


# ---- kill switches --------------------------------------------------------

def test_daily_drawdown_trips_and_latches_for_a_day():
    kill = KillSwitch()
    now = time.time()
    assert kill.check_daily_drawdown(1000, 969, limit_pct=3, now=now)
    assert kill.is_engaged(now + 3600)
    assert not kill.is_engaged(now + 25 * 3600)  # 24h halt, then it lifts


def test_drawdown_inside_the_limit_does_not_trip():
    assert not KillSwitch().check_daily_drawdown(1000, 980, limit_pct=3)


def test_feed_gap_halts_briefly_not_for_a_day():
    kill = KillSwitch()
    now = time.time()
    assert kill.check_feed_gap(45, limit_s=30, now=now)
    assert kill.is_engaged(now + 60) and not kill.is_engaged(now + 180)


def test_clock_skew_trips_before_the_hmac_headers_start_failing():
    assert KillSwitch().check_clock_skew(9.0, limit_s=5)
    assert not KillSwitch().check_clock_skew(1.0, limit_s=5)


def test_api_error_rate_trips_and_the_window_rolls():
    kill = KillSwitch()
    now = time.time()
    for _ in range(20):
        kill.record_api_error(now)
    assert kill.check_error_rate(limit_per_min=20, now=now)
    assert kill.error_rate_per_min(now + 120) == 0


def test_kill_switch_alerts_on_trip():
    seen = []
    kill = KillSwitch(on_trip=seen.append)
    kill.engage("test_reason")
    assert seen == ["test_reason"]


def test_clear_requires_an_explicit_call():
    kill = KillSwitch()
    kill.engage("whatever")
    assert kill.is_engaged()
    kill.clear()
    assert not kill.is_engaged()


# ---- gamma parsing --------------------------------------------------------

def test_parse_market_emits_one_market_per_token_with_complements():
    raw = {
        "conditionId": "0xcond",
        "question": "Will X happen?",
        "clobTokenIds": json.dumps(["tokA", "tokB"]),
        "outcomes": json.dumps(["Yes", "No"]),
        "liquidityNum": 50_000,
        "volume24hr": 20_000,
        "spread": 0.02,
        "endDate": "2026-12-31T00:00:00Z",
        "negRisk": False,
        "orderPriceMinTickSize": 0.01,
        "orderMinSize": 5,
        "events": [{"id": "evt1", "slug": "x", "tags": [{"slug": "politics"}]}],
        "description": "Resolves YES if X.",
    }
    markets = parse_market(raw)
    assert [m.token_id for m in markets] == ["tokA", "tokB"]
    assert markets[0].complement_token_id == "tokB"
    assert markets[0].tags == ["politics"] and markets[0].end_ts > 0
    assert markets[0].liquidity_usd == 50_000


def test_parse_market_tolerates_missing_fields():
    assert parse_market({"question": "no tokens"}) == []


# ---- spot feed ------------------------------------------------------------

def test_vol_needs_a_real_sample_before_it_returns_a_number():
    feed = SpotFeed(symbol="btcusdt")
    base = time.time() - 3600
    for i in range(10):
        feed.on_trade(100_000 * (1 + 0.001 * i), ts=base + i * 60)
    assert feed.annualised_vol(min_samples=30) is None


def test_vol_rises_with_volatility():
    calm, wild = SpotFeed(symbol="btcusdt"), SpotFeed(symbol="btcusdt")
    base = time.time() - 7200
    for i in range(60):
        calm.on_trade(100_000 * (1 + 0.0001 * ((-1) ** i)), ts=base + i * 60)
        wild.on_trade(100_000 * (1 + 0.01 * ((-1) ** i)), ts=base + i * 60)
    assert wild.annualised_vol(min_samples=30) > calm.annualised_vol(min_samples=30)


def test_binance_message_updates_the_feed():
    feed = BinanceFeed("wss://example", ["btcusdt"])
    feed._handle(json.dumps({"data": {"s": "BTCUSDT", "p": "101000.5", "T": 1_700_000_000_000}}))
    assert feed.price("btcusdt") == 101_000.5


# ---- rate limiting --------------------------------------------------------

async def test_token_bucket_throttles_a_burst():
    bucket = TokenBucket(rate_per_sec=50, capacity=2)
    start = time.monotonic()
    for _ in range(4):
        await bucket.acquire()
    assert time.monotonic() - start >= 0.03  # two free, two waited


# ---- backtest replay ------------------------------------------------------

def test_synthetic_book_is_two_sided_around_the_mid():
    book = synthetic_book("tok", 0.50, ts=time.time(), assumed_spread=0.02)
    assert book.best_bid == 0.49 and book.best_ask == 0.51
    assert not book.is_stale(60_000)


def test_replay_runs_a_strategy_over_a_price_series():
    from pmbot.config import MakerConfig
    from pmbot.strategies.s1_maker import MakerStrategy

    market = make_market(token_id="tok", end_ts=time.time() + 30 * 86400)
    strategy = MakerStrategy(MakerConfig(quote_size_usd=10), lambda token_id: [],
                             stale_book_max_ms=10 ** 9)
    series = [(int(time.time()) + i * 60, 0.50 + 0.01 * ((-1) ** i)) for i in range(50)]
    result = ReplayEngine(strategy, market, FeeTable(), starting_equity=1000).run(series)
    assert result.signals > 0
    assert len(result.equity_curve) == len(series)
