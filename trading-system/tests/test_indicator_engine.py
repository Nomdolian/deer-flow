from datetime import UTC, datetime, timedelta

from app.data.schema import Candle
from app.db.models import AssetClass, Direction
from app.signals.indicator_engine import IndicatorEngine, IndicatorEngineParams
from tests.conftest import make_candles


def test_no_signal_on_insufficient_bars():
    engine = IndicatorEngine(asset_class=AssetClass.forex)
    candles = make_candles(10)
    assert engine.evaluate(candles) is None


def _build_crossover_candles(n_flat: int, n_trend: int, base: float = 1.1000, d: float = 0.00015) -> list[Candle]:
    """A sideways chop (bounded, alternating up/down) followed by a net-uptrend
    phase with periodic pullbacks — real markets don't move in a straight line,
    and an engine tuned against monotonic synthetic data would be tuned wrong.
    The pullbacks keep RSI from instantly pegging at 100 while the EMAs still
    cross, matching the confluence window the engine actually looks for.
    """
    prices = [base]
    for i in range(n_flat):
        prices.append(prices[-1] + (d if i % 2 == 0 else -d))
    for i in range(n_trend):
        pattern = [d, d, d, -d * 1.4]
        prices.append(prices[-1] + pattern[i % 4])

    candles = []
    start = datetime(2024, 1, 1, tzinfo=UTC)
    for i in range(1, len(prices)):
        open_p, close_p = prices[i - 1], prices[i]
        candles.append(
            Candle(
                instrument="EURUSD",
                asset_class=AssetClass.forex,
                timeframe="H1",
                time=start + timedelta(hours=i),
                open=open_p,
                high=max(open_p, close_p) + 0.00005,
                low=min(open_p, close_p) - 0.00005,
                close=close_p,
                volume=100.0,
            )
        )
    return candles


def test_bullish_crossover_produces_long_signal():
    engine = IndicatorEngine(asset_class=AssetClass.forex, params=IndicatorEngineParams(min_bars=60))
    candles = _build_crossover_candles(n_flat=60, n_trend=60)

    signal = None
    for i in range(60, len(candles)):
        signal = engine.evaluate(candles[: i + 1])
        if signal is not None:
            break

    assert signal is not None
    assert signal.direction == Direction.long
    assert signal.stop_loss < signal.entry < signal.take_profit
    assert signal.strategy_id == "indicator_ema_rsi_atr"
    assert any("ema" in c for c in signal.confluences)


def test_deterministic_same_input_same_output():
    engine = IndicatorEngine(asset_class=AssetClass.forex)
    candles = make_candles(20, trend=0.0) + make_candles(60, trend=0.0005, start_price=1.11)
    result_a = engine.evaluate(candles)
    result_b = engine.evaluate(candles)
    assert result_a == result_b
