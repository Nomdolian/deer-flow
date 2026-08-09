from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import DecisionLog
from app.execution.base import ExecutionAdapter, OpenPositionSnapshot, OrderResult
from app.health.watchdog import ConnectivityWatchdog
from app.killswitch.service import is_engaged


def _session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


class _FlakyAdapter(ExecutionAdapter):
    name = "flaky"

    def __init__(self):
        self.connected = True
        self.last_connect_error = "simulated outage"

    def is_connected(self) -> bool:
        return self.connected

    def place_order(self, request) -> OrderResult:
        raise NotImplementedError

    def modify_order(self, client_order_id, *, stop_loss=None, take_profit=None) -> OrderResult:
        raise NotImplementedError

    def close_position(self, client_order_id) -> OrderResult:
        raise NotImplementedError

    def get_open_positions(self) -> list[OpenPositionSnapshot]:
        return []

    def get_equity(self) -> float:
        return 10_000.0


@pytest.fixture
def setup():
    session_local = _session_factory()
    adapter = _FlakyAdapter()
    return session_local, adapter


def test_brief_disconnect_does_not_engage_the_kill_switch(setup):
    """A WiFi blip must not halt trading — orders already fail closed while
    disconnected, so tripping the switch on every blip would make the system
    unusable and train you to re-enable it blindly."""
    session_local, adapter = setup
    watchdog = ConnectivityWatchdog(session_local, adapter, max_disconnected_seconds=300)

    adapter.connected = False
    assert watchdog.check_connectivity() is False

    session = session_local()
    assert is_engaged(session) is False
    session.close()


def test_prolonged_disconnect_engages_the_kill_switch(setup):
    session_local, adapter = setup
    watchdog = ConnectivityWatchdog(session_local, adapter, max_disconnected_seconds=300)

    adapter.connected = False
    watchdog.check_connectivity()  # starts the outage clock

    # Backdate the outage past the threshold rather than sleeping.
    watchdog._disconnected_since = datetime.now(UTC) - timedelta(seconds=301)
    watchdog.check_connectivity()

    session = session_local()
    assert is_engaged(session) is True
    session.close()


def test_reconnect_is_logged_but_does_not_auto_disengage(setup):
    """Manual re-enable is deliberate: a system that silently resumed after an
    unexplained outage would hide the outage from you entirely."""
    session_local, adapter = setup
    watchdog = ConnectivityWatchdog(session_local, adapter, max_disconnected_seconds=300)

    adapter.connected = False
    watchdog.check_connectivity()
    watchdog._disconnected_since = datetime.now(UTC) - timedelta(seconds=301)
    watchdog.check_connectivity()

    adapter.connected = True
    assert watchdog.check_connectivity() is True

    session = session_local()
    assert is_engaged(session) is True, "kill switch must stay engaged until manually cleared"
    events = {e.event_type for e in session.query(DecisionLog).all()}
    assert "broker_reconnected" in events
    session.close()


def test_outage_clock_resets_after_recovery(setup):
    session_local, adapter = setup
    watchdog = ConnectivityWatchdog(session_local, adapter, max_disconnected_seconds=300)

    adapter.connected = False
    watchdog.check_connectivity()
    assert watchdog.disconnected_since is not None

    adapter.connected = True
    watchdog.check_connectivity()
    assert watchdog.disconnected_since is None


def test_repeated_execution_errors_engage_the_kill_switch(setup):
    session_local, adapter = setup
    watchdog = ConnectivityWatchdog(session_local, adapter, max_consecutive_execution_errors=3)

    watchdog.record_execution_result(ok=False, detail="no trading permission")
    watchdog.record_execution_result(ok=False, detail="no trading permission")
    session = session_local()
    assert is_engaged(session) is False
    session.close()

    watchdog.record_execution_result(ok=False, detail="no trading permission")

    session = session_local()
    assert is_engaged(session) is True
    session.close()


def test_a_success_resets_the_execution_error_streak(setup):
    session_local, adapter = setup
    watchdog = ConnectivityWatchdog(session_local, adapter, max_consecutive_execution_errors=3)

    watchdog.record_execution_result(ok=False)
    watchdog.record_execution_result(ok=False)
    watchdog.record_execution_result(ok=True)  # streak broken
    watchdog.record_execution_result(ok=False)
    watchdog.record_execution_result(ok=False)

    session = session_local()
    assert is_engaged(session) is False
    session.close()
