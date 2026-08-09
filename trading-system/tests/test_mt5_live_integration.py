"""End-to-end test of the live MT5 path: MT5 data -> signal engines -> risk
manager -> MT5 execution -> journal, plus restart recovery.

This exercises the same objects `scripts/run_mt5_live.py` builds, with the
Windows-only MetaTrader5 package swapped for the fake. Without this the entire
live path would only ever be validated by pointing it at a funded account.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.data.providers.mt5_provider import MT5Provider
from app.db.base import Base
from app.db.models import AssetClass, Direction, OrderRecord, TradeJournalRecord
from app.execution.mt5_adapter import MT5Adapter
from app.health.watchdog import ConnectivityWatchdog
from app.journal.reconcile import reconcile_positions
from app.killswitch.service import is_engaged
from app.signals.base import Signal, SignalEngine
from app.watchlist.runner import WatchlistRunner
from app.watchlist.service import add_instrument
from tests import fake_mt5


@pytest.fixture(autouse=True)
def _reset_fake():
    fake_mt5.reset()
    yield
    fake_mt5.reset()


@pytest.fixture
def session_local():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


class _AlwaysLong(SignalEngine):
    def __init__(self, asset_class: AssetClass):
        self.strategy_id = "always_long"
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
            stop_loss=last.close * 0.99,
            take_profit=last.close * 1.02,
            confluences=["always"],
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            candle_time=last.time,
        )


def _build_runner(session_local, *, instruments):
    session = session_local()
    for instrument, asset_class in instruments:
        add_instrument(
            session,
            instrument=instrument,
            asset_class=asset_class,
            timeframe="M15",
            data_source="mt5",
        )
    session.close()

    execution = MT5Adapter(mt5_module=fake_mt5)
    return WatchlistRunner(
        session_factory=session_local,
        execution=execution,
        provider_factory=lambda ds, ac: MT5Provider(asset_class=ac, mt5_module=fake_mt5),
        engines_factory=lambda ac: [_AlwaysLong(ac)],
        watchdog=ConnectivityWatchdog(session_local, execution),
    ), execution


def test_full_live_path_places_an_mt5_order_and_journals_it(session_local):
    fake_mt5.add_symbol("EURUSD", 1.10)
    runner, execution = _build_runner(session_local, instruments=[("EURUSD", AssetClass.forex)])

    results = runner.run_once()

    assert results["_portfolio"]["connected"] is True
    positions = execution.get_open_positions()
    assert len(positions) == 1
    assert positions[0].instrument == "EURUSD"

    session = session_local()
    order = session.query(OrderRecord).one()
    assert order.status == "filled"
    assert order.broker == "mt5"
    assert order.broker_position_id is not None, "the broker ticket must be persisted for restart recovery"
    trade = session.query(TradeJournalRecord).one()
    assert trade.closed_at is None
    session.close()


def test_forex_and_crypto_trade_together_on_one_account(session_local):
    """Crypto CFDs run 24/7 while forex is ~24/5, so both must be able to be
    live simultaneously against the same account and the same risk caps."""
    fake_mt5.add_symbol("EURUSD", 1.10)
    fake_mt5.add_symbol("BTCUSD", 64000.0)
    runner, execution = _build_runner(
        session_local,
        instruments=[("EURUSD", AssetClass.forex), ("BTCUSD", AssetClass.crypto_major)],
    )

    runner.run_once()

    traded = {p.instrument for p in execution.get_open_positions()}
    assert traded == {"EURUSD", "BTCUSD"}


def test_weekend_closed_forex_does_not_trade_but_crypto_still_does(session_local):
    """The 24/5 vs 24/7 distinction, end to end: with forex untradeable
    (weekend) and crypto open, only crypto should trade — and the forex skip
    must be recorded as an expected market closure, not a fault."""
    fake_mt5.add_symbol("EURUSD", 1.10, tradeable=False)  # weekend
    fake_mt5.add_symbol("BTCUSD", 64000.0, tradeable=True)  # crypto never sleeps
    runner, execution = _build_runner(
        session_local,
        instruments=[("EURUSD", AssetClass.forex), ("BTCUSD", AssetClass.crypto_major)],
    )

    runner.run_once()

    traded = {p.instrument for p in execution.get_open_positions()}
    assert traded == {"BTCUSD"}

    session = session_local()
    from app.db.models import DecisionLog

    skips = [
        e
        for e in session.query(DecisionLog).filter_by(event_type="cycle_skipped").all()
        if e.instrument == "EURUSD"
    ]
    assert skips, "the closed-market skip must be logged"
    assert skips[-1].payload["reason"] == "market_closed"
    session.close()


def test_position_closed_by_broker_while_down_is_recovered_on_restart(session_local):
    """The full restart story: trade opens, process dies, the broker's
    stop-loss fires while nothing is running, and the next start reconciles it
    into the journal with the real exit price."""
    fake_mt5.add_symbol("EURUSD", 1.10)
    runner, execution = _build_runner(session_local, instruments=[("EURUSD", AssetClass.forex)])

    runner.run_once()
    ticket = int(execution.get_open_positions()[0].broker_position_id)

    # Process is down; the broker closes the position at the stop.
    fake_mt5.close_position_externally(ticket, exit_price=1.089)

    # Fresh start: new adapter, new session — nothing carried in memory.
    restarted_execution = MT5Adapter(mt5_module=fake_mt5)
    session = session_local()
    report = reconcile_positions(session, restarted_execution)
    session.close()

    assert len(report.closed_while_offline) == 1

    session = session_local()
    trade = session.query(TradeJournalRecord).one()
    assert trade.closed_at is not None
    assert trade.exit_price == 1.089
    assert trade.outcome == "loss"
    session.close()


def test_prolonged_outage_engages_the_kill_switch_and_stops_trading(session_local):
    from datetime import UTC, datetime, timedelta

    fake_mt5.add_symbol("EURUSD", 1.10)
    runner, execution = _build_runner(session_local, instruments=[("EURUSD", AssetClass.forex)])

    fake_mt5.state.broker_connected = False
    runner.run_once()  # starts the outage clock
    runner.watchdog._disconnected_since = datetime.now(UTC) - timedelta(seconds=99_999)
    runner.run_once()

    session = session_local()
    assert is_engaged(session) is True
    session.close()

    # Link restored, but the kill switch stays engaged until manually cleared.
    fake_mt5.state.broker_connected = True
    runner.run_once()
    assert execution.get_open_positions() == [], "must not trade while the kill switch is engaged"
