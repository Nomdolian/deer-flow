from app.data.schema import Candle
from app.db.models import AssetClass
from app.signals.smc_ict import (
    SwingKind,
    detect_fair_value_gaps,
    detect_swings,
    premium_discount_zone,
)
from tests.conftest import make_candles


def test_detect_swings_finds_local_extremes():
    candles = make_candles(20)
    swings = detect_swings(candles, left=2, right=2)
    assert all(0 <= s.index < len(candles) for s in swings)
    # every swing price must actually be the extreme of its local window
    for s in swings:
        window = candles[max(0, s.index - 2) : s.index + 3]
        if s.kind == SwingKind.high:
            assert s.price == max(c.high for c in window)
        else:
            assert s.price == min(c.low for c in window)


def test_fair_value_gap_detected_on_synthetic_imbalance():
    base = make_candles(5)
    # Craft a 3-candle imbalance: candle[1].high < candle[3].low
    c0, c1, _, _, c4 = base
    impulse = Candle(
        instrument=c1.instrument,
        asset_class=c1.asset_class,
        timeframe=c1.timeframe,
        time=c1.time,
        open=c1.close,
        high=c1.close + 0.01,
        low=c1.close,
        close=c1.close + 0.008,
        volume=100,
    )
    gap_candle = Candle(
        instrument=c1.instrument,
        asset_class=c1.asset_class,
        timeframe=c1.timeframe,
        time=c4.time,
        open=impulse.close,
        high=impulse.close + 0.002,
        low=impulse.close - 0.0005,  # strictly above c0.high to leave a real gap
        close=impulse.close + 0.001,
        volume=100,
    )
    candles = [c0, impulse, gap_candle]
    gaps = detect_fair_value_gaps(candles)
    assert len(gaps) == 1
    assert gaps[0].direction.value == "long"
    assert gaps[0].bottom == c0.high
    assert gaps[0].top == gap_candle.low


def test_premium_discount_zone():
    assert premium_discount_zone(dealing_high=1.20, dealing_low=1.10, price=1.16) == "premium"
    assert premium_discount_zone(dealing_high=1.20, dealing_low=1.10, price=1.14) == "discount"
    assert premium_discount_zone(dealing_high=1.20, dealing_low=1.10, price=1.15) == "equilibrium"


def test_engine_does_not_raise_on_flat_random_data():
    from app.signals.smc_ict import SMCICTEngine

    engine = SMCICTEngine(asset_class=AssetClass.forex)
    candles = make_candles(100, trend=0.0)
    # Must not raise regardless of whether a signal fires — determinism/no exceptions is the bar.
    result_a = engine.evaluate(candles)
    result_b = engine.evaluate(candles)
    assert result_a == result_b
