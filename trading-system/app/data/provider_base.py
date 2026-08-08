from abc import ABC, abstractmethod
from collections.abc import Iterable

from app.data.schema import Candle


class DataProvider(ABC):
    """Common interface every market-data source implements, so the rest of the
    system (signal engines, backtester) never cares whether a candle came from a
    broker WS feed, ccxt, or a CSV file."""

    name: str

    @abstractmethod
    def historical(self, instrument: str, timeframe: str, limit: int) -> list[Candle]:
        """Return the most recent `limit` closed candles, oldest first."""

    @abstractmethod
    def latest(self, instrument: str, timeframe: str) -> Candle | None:
        """Return the most recently closed candle, or None if unavailable."""

    def stream(self, instrument: str, timeframe: str) -> Iterable[Candle]:
        """Optional: live streaming. Default implementation is unsupported —
        providers used only for backtesting need not implement this."""
        raise NotImplementedError(f"{self.name} does not support streaming")
