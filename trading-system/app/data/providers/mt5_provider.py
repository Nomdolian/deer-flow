from datetime import UTC, datetime

from app.config import settings
from app.data.provider_base import DataProvider
from app.data.schema import Candle
from app.db.models import AssetClass

_TIMEFRAME_MAP_NAMES = {
    "M1": "TIMEFRAME_M1",
    "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}


class MT5Provider(DataProvider):
    """Live forex/indices/metals feed via the MetaTrader5 Python API.

    MetaTrader5's Python package only ships a working native extension on
    Windows, and the always-on server in this architecture is Linux (per Phase 0,
    item 1). In production this typically runs as a small Windows VM/container
    alongside the Linux server, or is swapped for a broker's native REST/FIX API
    (e.g. OANDA) that works cross-platform. The import is deferred and guarded so
    the rest of the system can be developed/tested on Linux without it installed.
    """

    name = "mt5"

    def __init__(self, asset_class: AssetClass = AssetClass.forex):
        try:
            import MetaTrader5 as mt5
        except ImportError as exc:
            raise RuntimeError(
                "MetaTrader5 package not installed/available on this platform. "
                "Install the trading-system[mt5] extra on a Windows host, or use "
                "a cross-platform broker API (OANDA, etc.) instead."
            ) from exc

        self._mt5 = mt5
        self.asset_class = asset_class
        if not mt5.initialize(
            login=int(settings.mt5_login) if settings.mt5_login else None,
            password=settings.mt5_password or None,
            server=settings.mt5_server or None,
            path=settings.mt5_path or None,
        ):
            raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")

    def _timeframe(self, timeframe: str):
        attr = _TIMEFRAME_MAP_NAMES.get(timeframe)
        if attr is None:
            raise ValueError(f"unsupported timeframe {timeframe}")
        return getattr(self._mt5, attr)

    def historical(self, instrument: str, timeframe: str, limit: int) -> list[Candle]:
        rates = self._mt5.copy_rates_from_pos(instrument, self._timeframe(timeframe), 0, limit)
        if rates is None:
            raise RuntimeError(f"MT5 copy_rates_from_pos failed for {instrument}: {self._mt5.last_error()}")
        return [
            Candle(
                instrument=instrument,
                asset_class=self.asset_class,
                timeframe=timeframe,
                time=datetime.fromtimestamp(int(r["time"]), tz=UTC),
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                volume=float(r["tick_volume"]),
            )
            for r in rates
        ]

    def latest(self, instrument: str, timeframe: str) -> Candle | None:
        candles = self.historical(instrument, timeframe, limit=2)
        # position 0 in MT5's "from_pos" is the CURRENT (still-forming) candle;
        # the last fully closed candle is one back — never signal off an unclosed bar.
        return candles[-2] if len(candles) >= 2 else None
