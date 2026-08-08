"""Run the orchestrator loop against a live (or CSV-replayed) feed, executing
through the in-process PaperAdapter simulator. This is what Build Order step 3
means by "paper/demo trading only, running for a meaningful sample size before
any real capital" — it must run continuously on the always-on server, not on a
laptop that sleeps.

    python -m scripts.run_live_paper --instrument EURUSD --timeframe H1 \
        --source mt5 --starting-balance 10000

    python -m scripts.run_live_paper --instrument EURUSD --timeframe D1 \
        --source alphavantage --asset-class forex --starting-balance 10000

--source alphavantage requires ALPHA_VANTAGE_API_KEY in .env. Its free tier
only serves DAILY bars for forex/crypto (--timeframe D1) — intraday FX/crypto
needs a paid Alpha Vantage plan; equity intraday is free but rate-limited.
See app/data/providers/alpha_vantage_provider.py.

Use --source csv (with --data-dir) to dry-run against local history without a
live connection, replaying it candle-by-candle.
"""

import argparse
import time

from app.config import settings
from app.data.provider_base import DataProvider
from app.db.base import SessionLocal, init_db
from app.db.models import AssetClass
from app.execution.paper_adapter import PaperAdapter
from app.orchestrator import Orchestrator
from app.signals.indicator_engine import IndicatorEngine
from app.signals.smc_ict import SMCICTEngine

_TIMEFRAME_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}
# A feed is only "stale" once it's missed several of its own candle intervals —
# not a fixed wall-clock number. A daily feed (D1) only produces one new candle
# every ~86400s, so the global 60s default would mark it stale on cycle two and
# the system would never trade; a 4x-interval buffer also comfortably absorbs a
# long weekend on forex D1 without being so loose it hides a genuinely dead feed.
_STALE_THRESHOLD_MULTIPLIER = 4


def build_provider(source: str, data_dir: str | None, asset_class: AssetClass) -> DataProvider:
    if source == "mt5":
        from app.data.providers.mt5_provider import MT5Provider

        return MT5Provider(asset_class=asset_class)
    if source == "alphavantage":
        from app.data.providers.alpha_vantage_provider import AlphaVantageProvider

        return AlphaVantageProvider(asset_class=asset_class)
    if source == "csv":
        if not data_dir:
            raise ValueError("--data-dir is required when --source csv")
        from app.data.providers.csv_provider import CSVProvider

        return CSVProvider(data_dir, asset_class)
    raise ValueError(f"unknown source {source!r}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--timeframe", default="H1")
    parser.add_argument("--source", default="mt5", choices=["mt5", "alphavantage", "csv"])
    parser.add_argument("--data-dir")
    parser.add_argument("--asset-class", default="forex", choices=[a.value for a in AssetClass])
    parser.add_argument("--starting-balance", type=float, default=10_000.0)
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args()

    init_db()
    asset_class = AssetClass(args.asset_class)
    provider = build_provider(args.source, args.data_dir, asset_class)
    execution = PaperAdapter(starting_balance=args.starting_balance)
    engines = [IndicatorEngine(asset_class=asset_class), SMCICTEngine(asset_class=asset_class)]

    candle_seconds = _TIMEFRAME_SECONDS.get(args.timeframe, 3600)
    feed_stale_seconds = max(settings.feed_stale_seconds, candle_seconds * _STALE_THRESHOLD_MULTIPLIER)

    orchestrator = Orchestrator(
        session_factory=SessionLocal,
        data_provider=provider,
        engines=engines,
        execution=execution,
        instrument=args.instrument,
        timeframe=args.timeframe,
        feed_stale_seconds=feed_stale_seconds,
    )

    print(
        f"starting paper-trading loop for {args.instrument} {args.timeframe} "
        f"(source={args.source}, feed_stale_seconds={feed_stale_seconds})"
    )
    try:
        while True:
            orchestrator.run_once()
            print(f"equity={execution.get_equity():.2f} open_positions={len(execution.get_open_positions())}")
            time.sleep(min(args.poll_seconds, candle_seconds))
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
