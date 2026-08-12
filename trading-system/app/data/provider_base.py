from abc import ABC, abstractmethod

from app.data.schema import Candle


class DataProvider(ABC):
    """Common interface every market-data source implements.

    Providers return only CLOSED candles, oldest-first, most-recent last — the
    same ordering `SignalEngine.evaluate` documents, so the identical engine code
    can be fed live or historical bars with no branching.

    Implementations must raise on failure rather than returning stale or partial
    data: the orchestrator fails closed on a data error, and a provider that
    silently returns an old bar would defeat that.
    """

    name: str

    @abstractmethod
    def historical(self, instrument: str, timeframe: str, limit: int) -> list[Candle]:
        """The most recent `limit` closed candles, oldest-first."""

    @abstractmethod
    def latest(self, instrument: str, timeframe: str) -> Candle | None:
        """The most recently closed candle, or None if none is available."""
