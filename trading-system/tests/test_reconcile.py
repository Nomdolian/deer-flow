from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import AssetClass, Direction, OrderRecord, TradeJournalRecord
from app.execution.mt5_adapter import MT5Adapter
from app.journal.reconcile import reconcile_positions
from tests import fake_mt5


@pytest.fixture(autouse=True)
def _reset_fake():
    fake_mt5.reset()
    yield
    fake_mt5.reset()


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _seed_open_trade(session, *, broker_position_id: str, instrument="EURUSD", entry=1.10, stop=1.09, tp=1.12):
    order = OrderRecord(
        client_order_id=f"cid-{broker_position_id}",
        instrument=instrument,
        direction=Direction.long,
        requested_size=10_000,
        requested_price=entry,
        filled_price=entry,
        stop_loss=stop,
        take_profit=tp,
        status="filled",
        broker="mt5",
        broker_position_id=broker_position_id,
        filled_at=datetime.now(UTC),
    )
    session.add(order)
    session.commit()
    session.refresh(order)

    trade = TradeJournalRecord(
        order_id=order.id,
        strategy_id="smc_ict_structure",
        strategy_version=1,
        instrument=instrument,
        asset_class=AssetClass.forex,
        direction=Direction.long,
        confluences=["bos_bullish"],
        size=10_000,
        entry_price=entry,
        stop_loss=stop,
        take_profit=tp,
        opened_at=datetime.now(UTC),
    )
    session.add(trade)
    session.commit()
    session.refresh(trade)
    return order, trade


def test_position_still_open_at_broker_is_left_alone(db):
    fake_mt5.add_symbol("EURUSD", 1.10)
    adapter = MT5Adapter(mt5_module=fake_mt5)
    ticket = fake_mt5.add_manual_position("EURUSD", magic=adapter.magic)
    _order, trade = _seed_open_trade(db, broker_position_id=str(ticket))

    report = reconcile_positions(db, adapter)

    assert report.still_open == [trade.id]
    assert report.closed_while_offline == []
    db.refresh(trade)
    assert trade.closed_at is None


def test_position_closed_while_offline_is_journaled_with_the_real_exit_price(db):
    """The core reason reconciliation exists: SL/TP live broker-side, so a
    trade can close while this process is down. Without this the journal keeps
    a phantom open trade forever, the risk manager keeps counting its risk
    against the portfolio cap, and the strategy never learns the outcome."""
    fake_mt5.add_symbol("EURUSD", 1.10)
    adapter = MT5Adapter(mt5_module=fake_mt5)
    ticket = fake_mt5.add_manual_position("EURUSD", magic=adapter.magic)
    _order, trade = _seed_open_trade(db, broker_position_id=str(ticket), entry=1.10, stop=1.09)

    fake_mt5.close_position_externally(ticket, exit_price=1.09)

    report = reconcile_positions(db, adapter)

    assert report.closed_while_offline == [trade.id]
    db.refresh(trade)
    assert trade.closed_at is not None
    assert trade.exit_price == 1.09
    assert trade.outcome == "loss"
    assert trade.r_multiple is not None


def test_missing_exit_price_is_left_unresolved_rather_than_invented(db):
    """A fabricated fill price would silently corrupt the expectancy stats the
    learning loop uses to decide which strategies keep running, so an
    unverifiable close must stay open and be reported instead."""
    fake_mt5.add_symbol("EURUSD", 1.10)
    adapter = MT5Adapter(mt5_module=fake_mt5)
    ticket = fake_mt5.add_manual_position("EURUSD", magic=adapter.magic)
    _order, trade = _seed_open_trade(db, broker_position_id=str(ticket))

    fake_mt5.state.positions.pop(ticket)  # gone, and no deal history recorded
    fake_mt5.state.history_available = False

    report = reconcile_positions(db, adapter)

    assert report.unresolved == [trade.id]
    assert report.closed_while_offline == []
    db.refresh(trade)
    assert trade.closed_at is None
    assert trade.exit_price is None


def test_untracked_broker_position_is_reported_never_adopted_or_closed(db):
    """A position the system didn't open may be a manual trade — reconciliation
    reports it and takes no action."""
    fake_mt5.add_symbol("EURUSD", 1.10)
    adapter = MT5Adapter(mt5_module=fake_mt5)
    ticket = fake_mt5.add_manual_position("EURUSD", magic=adapter.magic)

    report = reconcile_positions(db, adapter)

    assert len(report.untracked_at_broker) == 1
    assert str(ticket) in report.untracked_at_broker[0]
    assert ticket in fake_mt5.state.positions  # untouched
    assert db.query(TradeJournalRecord).count() == 0


def test_reconcile_survives_a_broker_read_failure(db):
    """Reconciliation must never block startup — if the broker can't be read,
    it reports that and lets the watchdog handle the disconnect."""
    fake_mt5.add_symbol("EURUSD", 1.10)
    adapter = MT5Adapter(mt5_module=fake_mt5)
    _seed_open_trade(db, broker_position_id="999")
    fake_mt5.state.broker_connected = False

    report = reconcile_positions(db, adapter)

    # Disconnected adapter reports no positions; the seeded trade can't be
    # verified either way, so it must not be closed on a guess.
    assert report.closed_while_offline == []
    assert db.query(TradeJournalRecord).one().closed_at is None


def test_reconcile_handles_a_mix_of_open_closed_and_untracked(db):
    fake_mt5.add_symbol("EURUSD", 1.10)
    fake_mt5.add_symbol("GBPUSD", 1.27)
    adapter = MT5Adapter(mt5_module=fake_mt5)

    open_ticket = fake_mt5.add_manual_position("EURUSD", magic=adapter.magic)
    closed_ticket = fake_mt5.add_manual_position("GBPUSD", magic=adapter.magic)
    untracked_ticket = fake_mt5.add_manual_position("EURUSD", magic=adapter.magic)

    _o1, still_open = _seed_open_trade(db, broker_position_id=str(open_ticket), instrument="EURUSD")
    _o2, closed = _seed_open_trade(db, broker_position_id=str(closed_ticket), instrument="GBPUSD", entry=1.27, stop=1.26, tp=1.29)

    fake_mt5.close_position_externally(closed_ticket, exit_price=1.29)

    report = reconcile_positions(db, adapter)

    assert report.still_open == [still_open.id]
    assert report.closed_while_offline == [closed.id]
    assert len(report.untracked_at_broker) == 1
    assert str(untracked_ticket) in report.untracked_at_broker[0]

    db.refresh(closed)
    assert closed.outcome == "win"
