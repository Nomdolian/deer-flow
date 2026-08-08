"""Run the orchestrator continuously against replayed historical data through
the PaperAdapter simulator — for demoing/dev-testing the live loop (signals,
risk checks, fills, journal entries all landing in Postgres and visible via
the API/mobile app) without a real broker/exchange connection.

    python -m scripts.run_replay_demo --instrument EURUSD --timeframe H1 \
        --interval-seconds 2

Historical data is generated as a synthetic random-walk series if no CSV is
found at data/{instrument}_{timeframe}.csv — this is NOT real market data and
must never be used to judge whether a strategy has an edge; use
scripts/run_backtest.py against real history for that.
"""

import argparse
import math
import random
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.data.providers.csv_provider import CSVProvider
from app.data.providers.replay_provider import ReplayProvider
from app.data.schema import Candle
from app.db.base import SessionLocal, init_db
from app.db.models import AssetClass
from app.execution.paper_adapter import PaperAdapter
from app.orchestrator import Orchestrator
from app.signals.indicator_engine import IndicatorEngine
from app.signals.smc_ict import SMCICTEngine


def _synthetic_candles(instrument: str, asset_class: AssetClass, timeframe: str, n: int, seed: int = 7) -> list[Candle]:
    rng = random.Random(seed)
    price = 1.1000
    start = datetime(2023, 1, 1, tzinfo=UTC)
    candles = []
    for i in range(n):
        drift = 0.00001 * math.sin(i / 150)
        shock = rng.gauss(0, 0.0004)
        close = price + drift + shock
        open_ = price
        high = max(open_, close) + abs(rng.gauss(0, 0.0002))
        low = min(open_, close) - abs(rng.gauss(0, 0.0002))
        candles.append(
            Candle(
                instrument=instrument,
                asset_class=asset_class,
                timeframe=timeframe,
                time=start + timedelta(hours=i),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=rng.uniform(80, 200),
            )
        )
        price = close
    return candles


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instrument", default="EURUSD")
    parser.add_argument("--timeframe", default="H1")
    parser.add_argument("--asset-class", default="forex", choices=[a.value for a in AssetClass])
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--starting-balance", type=float, default=10_000.0)
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    parser.add_argument("--candle-count", type=int, default=2000, help="synthetic data length if no CSV found")
    args = parser.parse_args()

    init_db()
    asset_class = AssetClass(args.asset_class)

    csv_path = Path(args.data_dir) / f"{args.instrument}_{args.timeframe}.csv"
    if csv_path.exists():
        candles = CSVProvider(args.data_dir, asset_class).all_candles(args.instrument, args.timeframe)
        print(f"replaying {len(candles)} candles from {csv_path}")
    else:
        candles = _synthetic_candles(args.instrument, asset_class, args.timeframe, args.candle_count)
        print(f"no CSV at {csv_path} — replaying {len(candles)} synthetic candles instead (NOT real market data)")

    provider = ReplayProvider(candles, start_at=60)
    execution = PaperAdapter(starting_balance=args.starting_balance)
    engines = [IndicatorEngine(asset_class=asset_class), SMCICTEngine(asset_class=asset_class)]

    orchestrator = Orchestrator(
        session_factory=SessionLocal,
        data_provider=provider,
        engines=engines,
        execution=execution,
        instrument=args.instrument,
        timeframe=args.timeframe,
    )

    print(f"starting replay loop for {args.instrument} {args.timeframe} — Ctrl+C to stop")
    cycle = 0
    try:
        while True:
            orchestrator.run_once()
            cycle += 1
            positions = execution.get_open_positions()
            print(
                f"cycle={cycle} equity={execution.get_equity():.2f} "
                f"open_positions={len(positions)} "
                + (f"[{positions[0].direction.value} {positions[0].instrument} @ {positions[0].entry_price:.5f}]" if positions else "")
            )
            time.sleep(args.interval_seconds)
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
