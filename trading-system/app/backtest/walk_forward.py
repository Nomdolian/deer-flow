from dataclasses import dataclass

from app.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from app.backtest.metrics import BacktestMetrics, compute_metrics
from app.data.schema import Candle
from app.signals.base import SignalEngine


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    train: list[Candle]
    test: list[Candle]


def split_windows(candles: list[Candle], n_splits: int, train_ratio: float = 0.7) -> list[WalkForwardWindow]:
    """Rolling walk-forward split: each window's test period is strictly later
    than its own train period, and never overlaps a later window's train period
    (Phase 7, item 3) — this only slices data; it does not itself retune
    parameters. Optimizing a signal engine's parameters per-window is a
    separate concern left to a parameter-search tool run against `train`, with
    `test` used purely for out-of-sample validation.
    """
    if n_splits < 1:
        raise ValueError("n_splits must be >= 1")
    n = len(candles)
    window_size = n // n_splits
    if window_size < 10:
        raise ValueError("not enough candles for the requested number of walk-forward splits")

    windows = []
    for i in range(n_splits):
        start = i * window_size
        end = n if i == n_splits - 1 else (i + 1) * window_size
        segment = candles[start:end]
        split_at = int(len(segment) * train_ratio)
        train, test = segment[:split_at], segment[split_at:]
        if train and test:
            windows.append(WalkForwardWindow(train=train, test=test))
    return windows


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    window: WalkForwardWindow
    train_result: BacktestResult
    test_result: BacktestResult
    train_metrics: BacktestMetrics
    test_metrics: BacktestMetrics


def run_walk_forward(
    engine: SignalEngine, candles: list[Candle], *, n_splits: int = 4, config: BacktestConfig | None = None
) -> list[WalkForwardResult]:
    """Never validate on the same data used to tune (Phase 7, item 3). For each
    window, this runs the identical engine against train and test separately so
    a strategy's test-period performance can be compared against its train-period
    performance — a large gap is exactly the overfitting signal to watch for."""
    cfg = config or BacktestConfig()
    engine_backtester = BacktestEngine(cfg)
    results = []
    for window in split_windows(candles, n_splits):
        train_result = engine_backtester.run(engine, window.train)
        test_result = engine_backtester.run(engine, window.test)
        results.append(
            WalkForwardResult(
                window=window,
                train_result=train_result,
                test_result=test_result,
                train_metrics=compute_metrics(train_result, cfg.starting_balance),
                test_metrics=compute_metrics(test_result, cfg.starting_balance),
            )
        )
    return results
