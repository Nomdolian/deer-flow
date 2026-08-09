from collections.abc import Callable

from app.config import settings
from app.data.provider_base import DataProvider
from app.db.models import AssetClass, WatchlistInstrument
from app.execution.base import ExecutionAdapter
from app.health.watchdog import ConnectivityWatchdog
from app.orchestrator import Orchestrator
from app.risk.manager import RiskManager
from app.signals.base import SignalEngine
from app.signals.indicator_engine import IndicatorEngine
from app.signals.smc_ict import SMCICTEngine
from app.watchlist.service import list_watchlist

_TIMEFRAME_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}
_STALE_THRESHOLD_MULTIPLIER = 4  # see scripts/run_live_paper.py for the reasoning


def _build_engines(asset_class: AssetClass) -> list[SignalEngine]:
    return [IndicatorEngine(asset_class=asset_class), SMCICTEngine(asset_class=asset_class)]


def _build_provider(data_source: str, asset_class: AssetClass) -> DataProvider:
    if data_source == "alphavantage":
        from app.data.providers.alpha_vantage_provider import AlphaVantageProvider

        return AlphaVantageProvider(asset_class=asset_class)
    if data_source == "mt5":
        from app.data.providers.mt5_provider import MT5Provider

        return MT5Provider(asset_class=asset_class)
    raise ValueError(f"unsupported watchlist data_source {data_source!r} (use alphavantage or mt5)")


class WatchlistRunner:
    """Runs the full data -> signal -> risk -> execution -> journal pipeline
    across every instrument on the watchlist, against ONE shared execution
    adapter/account — so the risk manager's portfolio and correlation-group
    caps actually apply across the selected assets, not per-instrument in
    isolation (Phase 3's whole point: "small trades taken consecutively
    without a portfolio-level exposure cap is functionally the same as one
    large trade").

    Enabling/disabling instruments takes effect on the next cycle without
    restarting: a newly enabled instrument gets its own Orchestrator built
    lazily; disabling one stops new signal evaluation for it while still
    monitoring (and correctly closing) any position it already has open —
    same "halt new, don't touch existing" rule as the kill switch.
    """

    def __init__(
        self,
        session_factory,
        execution: ExecutionAdapter,
        risk_manager: RiskManager | None = None,
        provider_factory: Callable[[str, AssetClass], DataProvider] | None = None,
        engines_factory: Callable[[AssetClass], list[SignalEngine]] | None = None,
        watchdog: ConnectivityWatchdog | None = None,
    ):
        self.session_factory = session_factory
        self.execution = execution
        self.risk_manager = risk_manager or RiskManager()
        self._provider_factory = provider_factory or _build_provider
        self._engines_factory = engines_factory or _build_engines
        self._orchestrators: dict[str, Orchestrator] = {}
        self._instrument_asset_classes: dict[str, AssetClass] = {}
        self.watchdog = watchdog

    def run_once(self) -> dict[str, dict]:
        # Check the broker link before doing anything. A disconnected cycle
        # must not be mistaken for "no signals today", and a prolonged outage
        # has to trip the kill switch (Phase 0, item 3).
        if self.watchdog is not None and not self.watchdog.check_connectivity():
            return {"_portfolio": {"connected": False, "equity": None, "open_positions": None}}

        session = self.session_factory()
        try:
            rows = list_watchlist(session)
        finally:
            session.close()

        self._instrument_asset_classes = {row.instrument: row.asset_class for row in rows}

        open_instruments = {p.instrument for p in self.execution.get_open_positions()}

        results: dict[str, dict] = {}
        for row in rows:
            needs_tracking = row.enabled or row.instrument in open_instruments
            if not needs_tracking:
                continue
            orchestrator = self._get_or_build(row)
            orchestrator.run_once(evaluate_new_signals=row.enabled)
            results[row.instrument] = {"enabled": row.enabled}

        results["_portfolio"] = {
            "connected": True,
            "equity": self.execution.get_equity(),
            "open_positions": len(self.execution.get_open_positions()),
        }
        return results

    def _get_or_build(self, row: WatchlistInstrument) -> Orchestrator:
        existing = self._orchestrators.get(row.instrument)
        if existing is not None:
            return existing

        provider = self._provider_factory(row.data_source, row.asset_class)
        candle_seconds = _TIMEFRAME_SECONDS.get(row.timeframe, 86400)
        feed_stale_seconds = max(settings.feed_stale_seconds, candle_seconds * _STALE_THRESHOLD_MULTIPLIER)

        orchestrator = Orchestrator(
            session_factory=self.session_factory,
            data_provider=provider,
            engines=self._engines_factory(row.asset_class),
            execution=self.execution,
            instrument=row.instrument,
            timeframe=row.timeframe,
            risk_manager=self.risk_manager,
            feed_stale_seconds=feed_stale_seconds,
            instrument_asset_classes=self._instrument_asset_classes,
        )
        self._orchestrators[row.instrument] = orchestrator
        return orchestrator
