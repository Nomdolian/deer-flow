import pytest

from app.data.providers.mt5_provider import MT5Provider
from app.db.models import AssetClass
from tests import fake_mt5


@pytest.fixture(autouse=True)
def _reset_fake():
    fake_mt5.reset()
    yield
    fake_mt5.reset()


def test_historical_returns_normalized_candles():
    fake_mt5.add_symbol("EURUSD", 1.10)
    provider = MT5Provider(asset_class=AssetClass.forex, mt5_module=fake_mt5)

    candles = provider.historical("EURUSD", "H1", limit=10)

    assert len(candles) == 10
    assert all(c.instrument == "EURUSD" for c in candles)
    assert all(c.asset_class == AssetClass.forex for c in candles)
    assert candles[0].time < candles[-1].time


def test_current_forming_candle_is_excluded():
    """copy_rates_from_pos index 0 is the still-forming bar. Letting it reach a
    signal engine would mean acting on an incomplete candle — a subtle form of
    lookahead that shows up as backtest/live divergence."""
    fake_mt5.add_symbol("EURUSD", 1.10)
    provider = MT5Provider(asset_class=AssetClass.forex, mt5_module=fake_mt5)

    requested = 5
    candles = provider.historical("EURUSD", "H1", limit=requested)

    # The fake returns `count` bars for a request of limit+1; the newest is dropped.
    assert len(candles) == requested
    raw = fake_mt5.copy_rates_from_pos("EURUSD", fake_mt5.TIMEFRAME_H1, 0, requested + 1)
    newest_raw_time = max(int(r["time"]) for r in raw)
    assert max(int(c.time.timestamp()) for c in candles) < newest_raw_time


def test_disconnected_terminal_raises_so_the_cycle_fails_closed():
    fake_mt5.add_symbol("EURUSD", 1.10)
    provider = MT5Provider(asset_class=AssetClass.forex, mt5_module=fake_mt5)

    fake_mt5.state.terminal_present = False
    fake_mt5.state.initialize_should_fail = True

    with pytest.raises(RuntimeError, match="not reachable"):
        provider.historical("EURUSD", "H1", limit=10)


def test_reconnects_after_a_transient_drop():
    fake_mt5.add_symbol("EURUSD", 1.10)
    provider = MT5Provider(asset_class=AssetClass.forex, mt5_module=fake_mt5)

    fake_mt5.state.broker_connected = False
    fake_mt5.state.broker_connected = True

    assert len(provider.historical("EURUSD", "H1", limit=3)) == 3


def test_unknown_symbol_raises_rather_than_returning_empty():
    """Silently returning [] would look like "no data yet" and hide a
    misconfigured broker symbol name forever."""
    fake_mt5.add_symbol("EURUSD", 1.10)
    provider = MT5Provider(asset_class=AssetClass.forex, mt5_module=fake_mt5)

    with pytest.raises(RuntimeError, match="no rates"):
        provider.historical("NOTREAL", "H1", limit=10)


def test_unsupported_timeframe_is_rejected():
    fake_mt5.add_symbol("EURUSD", 1.10)
    provider = MT5Provider(asset_class=AssetClass.forex, mt5_module=fake_mt5)

    with pytest.raises(ValueError, match="unsupported timeframe"):
        provider.historical("EURUSD", "W1", limit=10)


def test_crypto_asset_class_is_tagged_through():
    fake_mt5.add_symbol("BTCUSD", 64000.0)
    provider = MT5Provider(asset_class=AssetClass.crypto_major, mt5_module=fake_mt5)

    candles = provider.historical("BTCUSD", "M15", limit=5)

    assert all(c.asset_class == AssetClass.crypto_major for c in candles)
    assert all(c.timeframe == "M15" for c in candles)
