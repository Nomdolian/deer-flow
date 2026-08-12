from dataclasses import dataclass
from datetime import datetime

from app.db.models import AssetClass


@dataclass(frozen=True, slots=True)
class Candle:
    """One CLOSED OHLCV bar, as produced by any data provider.

    Immutable on purpose: signal engines receive lists of these and must not be
    able to mutate history out from under the backtester (the no-lookahead
    guarantee in SignalEngine relies on the window being read-only).

    `time` is the bar's OPEN time and must be timezone-aware UTC. Providers are
    responsible for only ever emitting bars that have finished — an in-progress
    bar would let an engine see a price that had not settled yet.
    """

    instrument: str
    asset_class: AssetClass
    timeframe: str
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open
