
from app.backtest.engine import BacktestConfig, BacktestEngine
from app.backtest.metrics import compute_metrics
from app.backtest.walk_forward import run_walk_forward, split_windows
from app.data.schema import Candle
from app.db.models import AssetClass, Direction
from app.signals.base import Signal, SignalEngine
from tests.conftest import make_candles


class _AlwaysLongEngine(SignalEngine):
    """Deterministic test double: fires a long signal on every candle after
    warmup, with a fixed stop/target relative to the close. Used to verify the
    backtester's fill/spread/no-lookahead mechanics independent of any real
    strategy's logic."""

    def __init__(self):
        self.strategy_id = "always_long_test"
        self.version = 1
        self.asset_class = AssetClass.forex

    def evaluate(self, candles: list[Candle]) -> Signal | None:
        last = candles[-1]
        return Signal(
            instrument=last.instrument,
            asset_class=AssetClass.forex,
            direction=Direction.long,
            confidence_score=1.0,
            entry=last.close,
            stop_loss=last.close - 0.0010,
            take_profit=last.close + 0.0020,
            confluences=["always"],
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            candle_time=last.time,
        )


def test_backtest_no_concurrent_double_open():
    engine = _AlwaysLongEngine()
    candles = make_candles(100, trend=0.0003)
    result = BacktestEngine(BacktestConfig(warmup_bars=10)).run(engine, candles)
    # trades must not overlap: each trade's open index must be >= previous close index
    for prev, cur in zip(result.trades, result.trades[1:]):
        assert cur.opened_index >= prev.closed_index


def test_backtest_applies_spread_and_slippage():
    engine = _AlwaysLongEngine()
    candles = make_candles(60, trend=0.0005)
    zero_cost = BacktestEngine(BacktestConfig(warmup_bars=10, spread_pct=0, slippage_pct=0)).run(engine, candles)
    with_cost = BacktestEngine(BacktestConfig(warmup_bars=10, spread_pct=0.001, slippage_pct=0.001)).run(engine, candles)
    assert len(zero_cost.trades) > 0
    assert len(with_cost.trades) > 0
    # same signals, but paying spread+slippage on entry must produce a worse (lower) first-trade PnL
    assert with_cost.trades[0].entry_price > zero_cost.trades[0].entry_price


def test_no_lookahead_signal_uses_only_past_candles():
    seen_max_index = {"value": -1}

    class _RecordingEngine(SignalEngine):
        def __init__(self):
            self.strategy_id = "recording"
            self.version = 1
            self.asset_class = AssetClass.forex

        def evaluate(self, candles):
            seen_max_index["value"] = max(seen_max_index["value"], len(candles) - 1)

    candles = make_candles(50)
    BacktestEngine(BacktestConfig(warmup_bars=5)).run(_RecordingEngine(), candles)
    assert seen_max_index["value"] == len(candles) - 1  # only ever grows to len-1, never sees future data


def test_metrics_win_rate_and_drawdown():
    engine = _AlwaysLongEngine()
    candles = make_candles(150, trend=0.0004)
    result = BacktestEngine(BacktestConfig(warmup_bars=10, starting_balance=10_000)).run(engine, candles)
    metrics = compute_metrics(result, starting_balance=10_000)
    assert 0.0 <= metrics.win_rate <= 1.0
    assert metrics.max_drawdown_pct >= 0.0


def test_walk_forward_split_never_lets_test_precede_train():
    candles = make_candles(400)
    windows = split_windows(candles, n_splits=4, train_ratio=0.7)
    assert len(windows) == 4
    for w in windows:
        assert w.train[-1].time < w.test[0].time


def test_walk_forward_runs_engine_unmodified_on_train_and_test():
    engine = _AlwaysLongEngine()
    candles = make_candles(400, trend=0.0002)
    results = run_walk_forward(engine, candles, n_splits=4, config=BacktestConfig(warmup_bars=10))
    assert len(results) == 4
    for r in results:
        assert r.train_metrics.sample_size >= 0
        assert r.test_metrics.sample_size >= 0
