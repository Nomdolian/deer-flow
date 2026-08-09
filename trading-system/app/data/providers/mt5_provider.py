import logging
from datetime import UTC, datetime

from app.config import settings
from app.data.provider_base import DataProvider
from app.data.schema import Candle
from app.db.models import AssetClass

_log = logging.getLogger("trading_system.mt5_provider")

_TIMEFRAME_ATTRS = {
    "M1": "TIMEFRAME_M1",
    "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}


class MT5Provider(DataProvider):
    """Market data straight from the MT5 terminal on this host — whatever your
    broker lists: forex, metals, indices, and crypto CFDs, all at real
    intraday timeframes with no separate data vendor or API key needed.

    Windows-only (the MetaTrader5 package has no working native extension
    elsewhere), so the import is deferred and injectable: pass `mt5_module` to
    exercise this against a fake on any platform rather than leaving it
    untested until it's on a live machine.

    Reconnects on demand. A dropped terminal connection surfaces as a
    RuntimeError from `historical()`, which the orchestrator already treats as
    fail-closed (skip the cycle, don't trade on data it couldn't verify).
    """

    name = "mt5"

    def __init__(self, asset_class: AssetClass = AssetClass.forex, mt5_module=None):
        if mt5_module is not None:
            self._mt5 = mt5_module
        else:
            try:
                import MetaTrader5 as mt5
            except ImportError as exc:
                raise RuntimeError(
                    "MetaTrader5 package not installed/available on this platform. "
                    "Install the trading-system[mt5] extra on a Windows host, or use "
                    "--source alphavantage for a cross-platform data feed."
                ) from exc
            self._mt5 = mt5

        self.asset_class = asset_class
        self._connect()

    def _connect(self) -> bool:
        mt5 = self._mt5
        kwargs = {}
        if settings.mt5_login:
            kwargs["login"] = int(settings.mt5_login)
        if settings.mt5_password:
            kwargs["password"] = settings.mt5_password
        if settings.mt5_server:
            kwargs["server"] = settings.mt5_server
        if settings.mt5_path:
            kwargs["path"] = settings.mt5_path
        try:
            return bool(mt5.initialize(**kwargs))
        except Exception:  # noqa: BLE001 - caller decides; never crash the loop here
            return False

    def _is_connected(self) -> bool:
        try:
            info = self._mt5.terminal_info()
        except Exception:  # noqa: BLE001
            return False
        return info is not None and getattr(info, "connected", True)

    def _ensure_connected(self) -> bool:
        if self._is_connected():
            return True
        return self._connect() and self._is_connected()

    def _select_symbol_if_needed(self, instrument: str) -> None:
        """Symbols absent from Market Watch return no data until selected.
        Best-effort: if this fails the fetch below reports the real problem
        with far better context than an exception from here would."""
        try:
            info = self._mt5.symbol_info(instrument)
            if info is not None and not getattr(info, "visible", True):
                self._mt5.symbol_select(instrument, True)
        except Exception as exc:  # noqa: BLE001 - diagnostic only, never fatal
            _log.debug("symbol_select(%s) failed, continuing to fetch: %s", instrument, exc)

    def _timeframe(self, timeframe: str):
        attr = _TIMEFRAME_ATTRS.get(timeframe)
        if attr is None:
            raise ValueError(f"unsupported timeframe {timeframe!r} for MT5 (have {sorted(_TIMEFRAME_ATTRS)})")
        return getattr(self._mt5, attr)

    def historical(self, instrument: str, timeframe: str, limit: int) -> list[Candle]:
        # Resolved up front, outside the try below: an unsupported timeframe is
        # a configuration mistake, and disguising it as a broker/data error
        # would send you debugging the connection instead of the typo.
        mt5_timeframe = self._timeframe(timeframe)

        if not self._ensure_connected():
            raise RuntimeError(f"MT5 terminal not reachable while fetching {instrument} {timeframe}")

        mt5 = self._mt5
        self._select_symbol_if_needed(instrument)

        # Fetch one extra bar: index 0 from copy_rates_from_pos is the CURRENT,
        # still-forming candle, which must never reach a signal engine.
        try:
            rates = mt5.copy_rates_from_pos(instrument, mt5_timeframe, 0, limit + 1)
        except Exception as exc:
            raise RuntimeError(f"MT5 copy_rates_from_pos raised for {instrument}: {exc}") from exc

        if rates is None or len(rates) == 0:
            raise RuntimeError(f"MT5 returned no rates for {instrument} {timeframe}: {mt5.last_error()}")

        closed = list(rates)[:-1] if len(rates) > 1 else []
        return [self._to_candle(instrument, timeframe, r) for r in closed][-limit:]

    def latest(self, instrument: str, timeframe: str) -> Candle | None:
        candles = self.historical(instrument, timeframe, limit=1)
        return candles[-1] if candles else None

    def _to_candle(self, instrument: str, timeframe: str, row) -> Candle:
        return Candle(
            instrument=instrument,
            asset_class=self.asset_class,
            timeframe=timeframe,
            time=datetime.fromtimestamp(int(row["time"]), tz=UTC),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["tick_volume"]),
        )
