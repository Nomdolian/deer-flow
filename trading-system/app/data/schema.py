from dataclasses import dataclass
from datetime import datetime

from app.db.models import AssetClass


@dataclass(frozen=True, slots=True)
class Candle:
    """The single internal candle schema every feed (crypto WS, MT5, OANDA,
    Alpaca/Polygon, DEX aggregators) gets normalized into, regardless of source."""

    instrument: str
    asset_class: AssetClass
    timeframe: str  # e.g. "M1", "M5", "H1", "D1"
    time: datetime  # candle OPEN time, UTC
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError(f"{self.instrument}: high {self.high} < low {self.low}")
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError(f"{self.instrument}: open/close outside high/low range")
