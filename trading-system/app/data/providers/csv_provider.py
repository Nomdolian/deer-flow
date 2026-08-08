from pathlib import Path

import pandas as pd

from app.data.provider_base import DataProvider
from app.data.schema import Candle
from app.db.models import AssetClass


class CSVProvider(DataProvider):
    """Local-history provider used for backtesting and for bootstrapping a new
    instrument before a live feed exists. Expects columns:
    time,open,high,low,close,volume (time = ISO8601 or epoch seconds, UTC).

    This is also where Phase 1's requirement to persist raw OHLCV independent of
    the broker/exchange's own retention is satisfied for offline use — the live
    ingestion service (Phase 1, item 1) writes into the same on-disk/DB store this
    reads from.
    """

    name = "csv"

    def __init__(self, data_dir: str | Path, asset_class: AssetClass):
        self.data_dir = Path(data_dir)
        self.asset_class = asset_class

    def _path(self, instrument: str, timeframe: str) -> Path:
        return self.data_dir / f"{instrument}_{timeframe}.csv"

    def _load(self, instrument: str, timeframe: str) -> pd.DataFrame:
        path = self._path(instrument, timeframe)
        if not path.exists():
            raise FileNotFoundError(f"no historical data for {instrument} {timeframe} at {path}")
        df = pd.read_csv(path)
        if pd.api.types.is_numeric_dtype(df["time"]):
            df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        else:
            df["time"] = pd.to_datetime(df["time"], utc=True)
        return df.sort_values("time").reset_index(drop=True)

    def historical(self, instrument: str, timeframe: str, limit: int) -> list[Candle]:
        df = self._load(instrument, timeframe).tail(limit)
        return [
            Candle(
                instrument=instrument,
                asset_class=self.asset_class,
                timeframe=timeframe,
                time=row.time.to_pydatetime(),
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume),
            )
            for row in df.itertuples()
        ]

    def all_candles(self, instrument: str, timeframe: str) -> list[Candle]:
        """Full history for backtesting — not part of the abstract interface since
        live providers wouldn't sensibly implement "all"."""
        df = self._load(instrument, timeframe)
        return [
            Candle(
                instrument=instrument,
                asset_class=self.asset_class,
                timeframe=timeframe,
                time=row.time.to_pydatetime(),
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume),
            )
            for row in df.itertuples()
        ]

    def latest(self, instrument: str, timeframe: str) -> Candle | None:
        candles = self.historical(instrument, timeframe, limit=1)
        return candles[0] if candles else None
