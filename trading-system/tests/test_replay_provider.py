from app.data.providers.replay_provider import ReplayProvider
from tests.conftest import make_candles


def test_historical_grows_by_one_candle_per_call():
    candles = make_candles(20)
    provider = ReplayProvider(candles, start_at=5)

    first = provider.historical("EURUSD", "H1", limit=100)
    second = provider.historical("EURUSD", "H1", limit=100)

    assert len(first) == 5
    assert len(second) == 6
    assert second[:-1] == first
    assert second[-1] == candles[5]


def test_historical_respects_limit_window():
    candles = make_candles(20)
    provider = ReplayProvider(candles, start_at=10)
    window = provider.historical("EURUSD", "H1", limit=3)
    assert len(window) == 3
    assert window == candles[7:10]


def test_loops_back_to_start_after_exhausting_series():
    candles = make_candles(10)
    provider = ReplayProvider(candles, start_at=8)
    provider.historical("EURUSD", "H1", limit=100)  # window len 8, pointer -> 9
    provider.historical("EURUSD", "H1", limit=100)  # window len 9, pointer -> 10 (== len)
    third = provider.historical("EURUSD", "H1", limit=100)  # window len 10 (full), pointer wraps -> start_at
    fourth = provider.historical("EURUSD", "H1", limit=100)  # should reflect the wrap
    assert len(third) == 10
    assert len(fourth) == 8


def test_latest_does_not_advance_pointer():
    candles = make_candles(20)
    provider = ReplayProvider(candles, start_at=5)
    a = provider.latest("EURUSD", "H1")
    b = provider.latest("EURUSD", "H1")
    assert a == b == candles[4]
