"""Walk-forward backtest a signal engine against local CSV history.

    python -m scripts.run_backtest --data-dir ./data --instrument EURUSD \
        --timeframe H1 --engine indicator --splits 4

Never optimize parameters on the same data you validate on (Phase 7, item 3) —
this prints train vs. test metrics per window specifically so a large gap
between them is visible.
"""

import argparse

from app.backtest.engine import BacktestConfig
from app.backtest.walk_forward import run_walk_forward
from app.data.providers.csv_provider import CSVProvider
from app.db.models import AssetClass
from app.signals.indicator_engine import IndicatorEngine
from app.signals.smc_ict import SMCICTEngine


def build_engine(name: str, asset_class: AssetClass):
    if name == "indicator":
        return IndicatorEngine(asset_class=asset_class)
    if name == "smc":
        return SMCICTEngine(asset_class=asset_class)
    raise ValueError(f"unknown engine {name!r}, expected 'indicator' or 'smc'")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--timeframe", default="H1")
    parser.add_argument("--engine", default="indicator", choices=["indicator", "smc"])
    parser.add_argument("--asset-class", default="forex", choices=[a.value for a in AssetClass])
    parser.add_argument("--splits", type=int, default=4)
    parser.add_argument("--starting-balance", type=float, default=10_000.0)
    args = parser.parse_args()

    asset_class = AssetClass(args.asset_class)
    provider = CSVProvider(args.data_dir, asset_class)
    candles = provider.all_candles(args.instrument, args.timeframe)
    print(f"loaded {len(candles)} candles for {args.instrument} {args.timeframe}")

    engine = build_engine(args.engine, asset_class)
    config = BacktestConfig(starting_balance=args.starting_balance)

    results = run_walk_forward(engine, candles, n_splits=args.splits, config=config)
    for i, r in enumerate(results):
        print(f"\n--- window {i + 1}/{len(results)} ---")
        print(f"  train: {len(r.window.train)} candles, {r.train_metrics.sample_size} trades, "
              f"win_rate={r.train_metrics.win_rate:.1%}, expectancy_r={r.train_metrics.expectancy_r:.2f}, "
              f"max_dd={r.train_metrics.max_drawdown_pct:.1%}")
        print(f"  test:  {len(r.window.test)} candles, {r.test_metrics.sample_size} trades, "
              f"win_rate={r.test_metrics.win_rate:.1%}, expectancy_r={r.test_metrics.expectancy_r:.2f}, "
              f"max_dd={r.test_metrics.max_drawdown_pct:.1%}")


if __name__ == "__main__":
    main()
