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
    name = "static"

    def __init__(self, candles: list[Candle]):
        self._candles = candles

    def historical(self, instrument, timeframe, limit):
        return self._candles[-limit:]

    def latest(self, instrument, timeframe):
        return self._candles[-1] if self._candles else None


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
