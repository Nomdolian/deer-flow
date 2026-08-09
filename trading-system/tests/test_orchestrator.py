from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.data.provider_base import DataProvider
from app.data.schema import Candle
from app.db.base import Base
from app.db.models import AssetClass, OrderRecord
from app.execution.paper_adapter import PaperAdapter
from app.killswitch.service import engage
from app.orchestrator import Orchestrator
from app.signals.base import Signal, SignalEngine
from tests.conftest import make_candles


class _StaticProvider(DataProvider):
    """Serves a fixed candle list. `candles` is public and reassignable so a
    test can advance the feed (e.g. to breach a stop) mid-run."""

    name = "static"

    def __init__(self, candles: list[Candle]):
        self.candles = candles

    def historical(self, instrument, timeframe, limit):
        return self.candles[-limit:]

    def latest(self, instrument, timeframe):
        return self.candles[-1] if self.candles else None


class _AlwaysSignalEngine(SignalEngine):
    def __init__(self):
        self.strategy_id = "always"
        self.version = 1
        self.asset_class = AssetClass.forex

    def evaluate(self, candles):
        last = candles[-1]
        return Signal(
            instrument=last.instrument,
            asset_class=AssetClass.forex,
            direction=__import__("app.db.models", fromlist=["Direction"]).Direction.long,
            confidence_score=1.0,
            entry=last.close,
            stop_loss=last.close - 0.001,
            take_profit=last.close + 0.002,
            confluences=["always"],
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            candle_time=last.time,
        )


def _session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_kill_switch_blocks_new_orders():
    session_local = _session_factory()
    candles = make_candles(80)
    orchestrator = Orchestrator(
        session_factory=session_local,
        data_provider=_StaticProvider(candles),
        engines=[_AlwaysSignalEngine()],
        execution=PaperAdapter(starting_balance=10_000),
        instrument="EURUSD",
        timeframe="H1",
    )
    session = session_local()
    engage(session, reason="test", triggered_by="test")
    session.close()

    orchestrator.run_once()

    session = session_local()
    assert session.query(OrderRecord).count() == 0
    session.close()


def test_orchestrator_places_and_journals_a_trade():
    session_local = _session_factory()
    candles = make_candles(80)
    execution = PaperAdapter(starting_balance=10_000)
    orchestrator = Orchestrator(
        session_factory=session_local,
        data_provider=_StaticProvider(candles),
        engines=[_AlwaysSignalEngine()],
        execution=execution,
        instrument="EURUSD",
        timeframe="H1",
    )

    orchestrator.run_once()

    session = session_local()
    orders = session.query(OrderRecord).all()
    assert len(orders) == 1
    assert orders[0].status == "filled"
    from app.db.models import TradeJournalRecord

    trades = session.query(TradeJournalRecord).all()
    assert len(trades) == 1
    assert trades[0].closed_at is None
    session.close()

    # a second cycle with an open position must not open a duplicate trade
    orchestrator.run_once()
    session = session_local()
    assert session.query(OrderRecord).count() == 1
    session.close()


def test_stale_feed_blocks_new_signal_generation():
    from app.db.models import FeedHealthRecord

    session_local = _session_factory()
    candles = make_candles(80)
    execution = PaperAdapter(starting_balance=10_000)
    orchestrator = Orchestrator(
        session_factory=session_local,
        data_provider=_StaticProvider(candles),
        engines=[_AlwaysSignalEngine()],
        execution=execution,
        instrument="EURUSD",
        timeframe="H1",
    )

    # Manually seed a stale feed-health row (as if the last successful poll was
    # over an hour ago) before the orchestrator ever runs, so this test doesn't
    # depend on real wall-clock sleeps or on the single-open-position guard.
    session = session_local()
    session.add(
        FeedHealthRecord(
            instrument="EURUSD",
            last_candle_time=candles[-1].time,
            last_seen_at=datetime.now(UTC) - timedelta(hours=2),
            is_stale=False,
        )
    )
    session.commit()
    session.close()

    orchestrator.run_once()

    session = session_local()
    assert session.query(OrderRecord).count() == 0
    row = session.get(FeedHealthRecord, "EURUSD")
    assert row.is_stale


def test_custom_feed_stale_seconds_overrides_the_default_for_daily_bars():
    """A daily-bar feed only produces one new candle every ~24h — with the
    global 60s default it would be marked stale forever. An orchestrator
    configured with a wider feed_stale_seconds (as scripts/run_live_paper.py
    computes from the timeframe) must still trade against the same 2-hour-old
    poll that the previous test correctly rejects under the default."""
    from app.db.models import FeedHealthRecord

    session_local = _session_factory()
    candles = make_candles(80)
    execution = PaperAdapter(starting_balance=10_000)
    orchestrator = Orchestrator(
        session_factory=session_local,
        data_provider=_StaticProvider(candles),
        engines=[_AlwaysSignalEngine()],
        execution=execution,
        instrument="EURUSD",
        timeframe="D1",
        feed_stale_seconds=86400 * 4,  # matches run_live_paper.py's D1 multiplier
    )

    session = session_local()
    session.add(
        FeedHealthRecord(
            instrument="EURUSD",
            last_candle_time=candles[-1].time,
            last_seen_at=datetime.now(UTC) - timedelta(hours=2),
            is_stale=False,
        )
    )
    session.commit()
    session.close()

    orchestrator.run_once()

    session = session_local()
    assert session.query(OrderRecord).count() == 1
    row = session.get(FeedHealthRecord, "EURUSD")
    assert not row.is_stale
    session.close()


def test_shared_execution_open_position_only_blocks_its_own_instrument():
    """One instrument having an open position must not block a different
    instrument's orchestrator from evaluating a new signal — this is exactly
    what a shared-account, multi-asset watchlist needs (WatchlistRunner)."""
    session_local = _session_factory()
    eur_candles = make_candles(80, instrument="EURUSD")
    gbp_candles = make_candles(80, instrument="GBPUSD")
    execution = PaperAdapter(starting_balance=10_000)

    eur_orch = Orchestrator(
        session_factory=session_local,
        data_provider=_StaticProvider(eur_candles),
        engines=[_AlwaysSignalEngine()],
        execution=execution,
        instrument="EURUSD",
        timeframe="H1",
    )
    gbp_orch = Orchestrator(
        session_factory=session_local,
        data_provider=_StaticProvider(gbp_candles),
        engines=[_AlwaysSignalEngine()],
        execution=execution,
        instrument="GBPUSD",
        timeframe="H1",
    )

    eur_orch.run_once()
    gbp_orch.run_once()

    session = session_local()
    instruments_traded = {o.instrument for o in session.query(OrderRecord).all()}
    session.close()
    assert instruments_traded == {"EURUSD", "GBPUSD"}


def test_disabled_instrument_still_gets_stop_loss_closed():
    """evaluate_new_signals=False (a disabled watchlist row) must not stop an
    already-open position from being monitored and closed — a real trader
    doesn't abandon a position just because they stopped looking for new
    setups on that symbol."""
    from app.db.models import Direction

    session_local = _session_factory()
    base_candles = make_candles(80)
    execution = PaperAdapter(starting_balance=10_000, spread_pct=0, slippage_pct=0)

    provider = _StaticProvider(base_candles)
    orchestrator = Orchestrator(
        session_factory=session_local,
        data_provider=provider,
        engines=[_AlwaysSignalEngine()],
        execution=execution,
        instrument="EURUSD",
        timeframe="H1",
    )

    orchestrator.run_once()  # opens a position
    position = execution.get_open_positions()[0]
    assert position.direction == Direction.long

    # Next candle's low breaches the stop-loss.
    stop_candle = Candle(
        instrument="EURUSD",
        asset_class=AssetClass.forex,
        timeframe="H1",
        time=base_candles[-1].time + timedelta(hours=1),
        open=position.entry_price,
        high=position.entry_price + 0.0005,
        low=position.stop_loss - 0.0001,
        close=position.entry_price,
        volume=100.0,
    )
    provider.candles = [*base_candles, stop_candle]

    orchestrator.run_once(evaluate_new_signals=False)

    assert execution.get_open_positions() == []


def test_shared_execution_tags_open_positions_with_their_own_asset_class():
    """Without instrument_asset_classes, an orchestrator would tag every open
    position with its OWN engines' asset class — silently mislabeling a BTCUSD
    position as forex when evaluated from a EURUSD orchestrator sharing the
    same account. This is exactly what corrupts the meme-bucket/portfolio-cap
    split in RiskManager for a multi-asset watchlist."""
    from app.db.models import Direction
    from app.execution.base import OrderRequest

    session_local = _session_factory()
    execution = PaperAdapter(starting_balance=10_000, spread_pct=0, slippage_pct=0)
    execution.update_price("EURUSD", 1.1000)
    execution.update_price("BTCUSD", 60000.0)
    execution.place_order(OrderRequest("o1", "EURUSD", Direction.long, size=1000, stop_loss=1.09, take_profit=1.12))
    execution.place_order(OrderRequest("o2", "BTCUSD", Direction.long, size=0.1, stop_loss=58000, take_profit=65000))

    asset_classes = {"EURUSD": AssetClass.forex, "BTCUSD": AssetClass.crypto_major}
    orchestrator = Orchestrator(
        session_factory=session_local,
        data_provider=_StaticProvider(make_candles(80)),
        engines=[_AlwaysSignalEngine()],  # asset_class=forex — would be wrong for BTCUSD without the mapping
        execution=execution,
        instrument="EURUSD",
        timeframe="H1",
        instrument_asset_classes=asset_classes,
    )

    session = session_local()
    portfolio = orchestrator._portfolio_state(session)
    session.close()

    tagged = {p.instrument: p.asset_class for p in portfolio.open_positions}
    assert tagged["EURUSD"] == AssetClass.forex
    assert tagged["BTCUSD"] == AssetClass.crypto_major


def test_open_trade_is_matched_to_its_journal_row_after_a_process_restart():
    """The bug this guards: the position->trade mapping used to live in a
    module-level dict, so a restart (Windows Update reboot, crash, lid close)
    orphaned every open position from its journal row — its close would go
    unrecorded and strategy stats would be silently wrong. The mapping must be
    a DB lookup that survives a fresh process."""
    from app.journal.service import find_open_trade
    from app.orchestrator import Orchestrator as FreshOrchestrator

    session_local = _session_factory()
    candles = make_candles(80, instrument="EURUSD")
    provider = _StaticProvider(candles)
    execution = PaperAdapter(starting_balance=10_000, spread_pct=0, slippage_pct=0)

    orchestrator = Orchestrator(
        session_factory=session_local,
        data_provider=provider,
        engines=[_AlwaysSignalEngine()],
        execution=execution,
        instrument="EURUSD",
        timeframe="H1",
    )
    orchestrator.run_once()

    position = execution.get_open_positions()[0]

    # Simulate a restart: brand-new Orchestrator instance, nothing carried over
    # in process memory. Only the DB persists.
    session = session_local()
    trade = find_open_trade(session, client_order_id=position.client_order_id)
    assert trade is not None, "open trade must be findable from the DB alone"
    session.close()

    restarted = FreshOrchestrator(
        session_factory=session_local,
        data_provider=provider,
        engines=[_AlwaysSignalEngine()],
        execution=execution,
        instrument="EURUSD",
        timeframe="H1",
    )

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

    restarted.run_once()

    session = session_local()
    closed = session.get(type(trade), trade.id)
    assert closed.closed_at is not None, "the close must be journaled after a restart"
    assert closed.pnl is not None
    session.close()
