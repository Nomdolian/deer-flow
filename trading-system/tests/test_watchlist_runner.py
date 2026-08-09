from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.data.provider_base import DataProvider
from app.data.schema import Candle
from app.db.base import Base
from app.db.models import AssetClass, Direction
from app.execution.paper_adapter import PaperAdapter
from app.signals.base import Signal, SignalEngine
from app.watchlist.runner import WatchlistRunner
from app.watchlist.service import add_instrument, set_enabled
from tests.conftest import make_candles


def _session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


class _AlwaysSignalEngine(SignalEngine):
    """Deterministic test double so these tests exercise the runner's
    orchestration (multi-instrument sharing, enable/disable) rather than
    depending on the real engines' technical conditions actually firing on
    generic synthetic candles."""

    def __init__(self, asset_class: AssetClass):
        self.strategy_id = "always"
        self.version = 1
        self.asset_class = asset_class

    def evaluate(self, candles):
        last = candles[-1]
        return Signal(
            instrument=last.instrument,
            asset_class=self.asset_class,
            direction=Direction.long,
            confidence_score=1.0,
            entry=last.close,
            stop_loss=last.close - 0.001,
            take_profit=last.close + 0.002,
            confluences=["always"],
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            candle_time=last.time,
        )


def _always_engines_factory(asset_class: AssetClass) -> list[SignalEngine]:
    return [_AlwaysSignalEngine(asset_class)]


class _StaticProvider(DataProvider):
    name = "static"

    def __init__(self, candles: list[Candle]):
        self.candles = candles

    def historical(self, instrument, timeframe, limit):
        return self.candles[-limit:]

    def latest(self, instrument, timeframe):
        return self.candles[-1] if self.candles else None


def _provider_factory_by_instrument(providers_by_instrument: dict[str, DataProvider]):
    """WatchlistRunner's provider_factory signature is (data_source, asset_class)
    — it doesn't know the instrument. Tests instead give the runner one
    provider per watchlist row by relying on rows being built in insertion
    order and consuming providers in that same order."""
    remaining = list(providers_by_instrument.values())

    def factory(data_source: str, asset_class: AssetClass) -> DataProvider:
        return remaining.pop(0)

    return factory


def test_runner_trades_multiple_enabled_instruments_on_one_shared_account():
    session_local = _session_factory()
    session = session_local()
    add_instrument(session, instrument="EURUSD", asset_class=AssetClass.forex, timeframe="H1", data_source="fake")
    add_instrument(session, instrument="GBPUSD", asset_class=AssetClass.forex, timeframe="H1", data_source="fake")
    session.close()

    providers = {
        "EURUSD": _StaticProvider(make_candles(80, instrument="EURUSD")),
        "GBPUSD": _StaticProvider(make_candles(80, instrument="GBPUSD")),
    }
    execution = PaperAdapter(starting_balance=10_000)
    runner = WatchlistRunner(
        session_factory=session_local,
        execution=execution,
        provider_factory=_provider_factory_by_instrument(providers),
        engines_factory=_always_engines_factory,
    )

    results = runner.run_once()

    assert "EURUSD" in results and "GBPUSD" in results
    open_instruments = {p.instrument for p in execution.get_open_positions()}
    assert open_instruments == {"EURUSD", "GBPUSD"}


def test_disabling_an_instrument_stops_new_signals_but_keeps_monitoring_open_one():
    session_local = _session_factory()
    session = session_local()
    add_instrument(session, instrument="EURUSD", asset_class=AssetClass.forex, timeframe="H1", data_source="fake")
    session.close()

    candles = make_candles(80, instrument="EURUSD")
    provider = _StaticProvider(candles)
    execution = PaperAdapter(starting_balance=10_000, spread_pct=0, slippage_pct=0)
    runner = WatchlistRunner(
        session_factory=session_local,
        execution=execution,
        provider_factory=lambda ds, ac: provider,
        engines_factory=_always_engines_factory,
    )

    runner.run_once()
    assert len(execution.get_open_positions()) == 1
    position = execution.get_open_positions()[0]

    session = session_local()
    set_enabled(session, "EURUSD", False)
    session.close()

    # Move price to breach the stop-loss; disabled but still open, so it must close.
    stop_candle = Candle(
        instrument="EURUSD",
        asset_class=AssetClass.forex,
        timeframe="H1",
        time=candles[-1].time + timedelta(hours=1),
        open=position.entry_price,
        high=position.entry_price + 0.0005,
        low=position.stop_loss - 0.0001,
        close=position.entry_price,
        volume=100.0,
    )
    provider.candles = [*candles, stop_candle]

    runner.run_once()

    assert execution.get_open_positions() == []


def test_removed_instrument_with_no_open_position_stops_being_tracked():
    session_local = _session_factory()
    session = session_local()
    add_instrument(session, instrument="EURUSD", asset_class=AssetClass.forex, timeframe="H1", data_source="fake")
    session.close()

    provider = _StaticProvider(make_candles(80, instrument="EURUSD"))
    execution = PaperAdapter(starting_balance=10_000, spread_pct=0, slippage_pct=0)
    runner = WatchlistRunner(
        session_factory=session_local,
        execution=execution,
        provider_factory=lambda ds, ac: provider,
        engines_factory=_always_engines_factory,
    )

    session = session_local()
    set_enabled(session, "EURUSD", False)
    session.close()

    results = runner.run_once()
    assert results == {"_portfolio": {"connected": True, "equity": 10_000.0, "open_positions": 0}}


def test_disconnected_broker_halts_the_whole_cycle_without_trading():
    """A disconnected cycle must not be mistaken for "no setups today" — and
    critically, must not place orders against data it can't verify."""
    from app.health.watchdog import ConnectivityWatchdog

    session_local = _session_factory()
    session = session_local()
    add_instrument(session, instrument="EURUSD", asset_class=AssetClass.forex, timeframe="H1", data_source="fake")
    session.close()

    provider = _StaticProvider(make_candles(80, instrument="EURUSD"))
    execution = PaperAdapter(starting_balance=10_000)

    class _DisconnectedAdapter:
        """Wraps the paper adapter but reports the broker link as down."""

        name = "disconnected"

        def __init__(self, inner):
            self._inner = inner

        def is_connected(self):
            return False

        def __getattr__(self, item):
            return getattr(self._inner, item)

    adapter = _DisconnectedAdapter(execution)
    runner = WatchlistRunner(
        session_factory=session_local,
        execution=adapter,
        provider_factory=lambda ds, ac: provider,
        engines_factory=_always_engines_factory,
        watchdog=ConnectivityWatchdog(session_local, adapter),
    )

    results = runner.run_once()

    assert results["_portfolio"]["connected"] is False
    assert execution.get_open_positions() == [], "must not trade while disconnected"
