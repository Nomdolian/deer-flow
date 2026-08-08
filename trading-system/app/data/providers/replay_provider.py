from app.data.provider_base import DataProvider
from app.data.schema import Candle


class ReplayProvider(DataProvider):
    """Replays a fixed historical candle series forward in simulated time —
    each call to `historical()` reveals exactly one more candle than the
    last, the same way a live feed delivers one new closed candle per poll.

    This exists for demoing/dev-testing the orchestrator's live loop without
    a real broker/exchange connection (no forex feed credentials configured
    in this environment). It is historical data on a compressed timeline, not
    a live market — never treat its output as a performance claim. Loops back
    to `start_at` when it reaches the end so a demo can run indefinitely.
    """

    name = "replay"

    def __init__(self, candles: list[Candle], start_at: int = 60):
        if start_at >= len(candles):
            raise ValueError("start_at must leave room for at least one candle")
        self._candles = candles
        self._start_at = start_at
        self._pointer = start_at

    def historical(self, instrument: str, timeframe: str, limit: int) -> list[Candle]:
        end = self._pointer
        start = max(0, end - limit)
        window = self._candles[start:end]
        self._pointer += 1
        if self._pointer > len(self._candles):
            self._pointer = self._start_at
        return window

    def latest(self, instrument: str, timeframe: str) -> Candle | None:
        if self._pointer == 0:
            return None
        return self._candles[self._pointer - 1]
