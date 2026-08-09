from datetime import UTC, datetime

from app.execution.base import ExecutionAdapter
from app.killswitch.service import engage, is_engaged
from app.logging_utils import log_decision
from app.notifications.service import send_push

# Generous by default: an ordinary WiFi blip or a broker's brief rollover
# hiccup should NOT halt trading, because orders already fail closed while
# disconnected (MT5Adapter.ensure_connected returns False and nothing is
# sent). The kill switch is for "something is actually wrong and staying
# wrong", not for transient noise.
DEFAULT_MAX_DISCONNECTED_SECONDS = 300
DEFAULT_MAX_CONSECUTIVE_EXECUTION_ERRORS = 5


class ConnectivityWatchdog:
    """Implements the automatic kill-switch triggers the spec requires
    (Phase 0, item 3): connectivity loss to the broker beyond a threshold, and
    repeated execution errors.

    Deliberately does NOT auto-disengage on recovery. The kill switch requires
    manual re-enable by design — if the system silently resumed after an
    unexplained multi-minute outage, you'd never know it happened. Re-enabling
    is one tap in the mobile app.

    Important safety property this relies on: every order is placed with its
    stop-loss and take-profit attached broker-side, so open positions stay
    protected even if this process is dead, the laptop is asleep, or the kill
    switch is engaged. Halting only stops NEW orders; it never leaves an
    existing position unguarded.
    """

    def __init__(
        self,
        session_factory,
        execution: ExecutionAdapter,
        *,
        max_disconnected_seconds: int = DEFAULT_MAX_DISCONNECTED_SECONDS,
        max_consecutive_execution_errors: int = DEFAULT_MAX_CONSECUTIVE_EXECUTION_ERRORS,
    ):
        self.session_factory = session_factory
        self.execution = execution
        self.max_disconnected_seconds = max_disconnected_seconds
        self.max_consecutive_execution_errors = max_consecutive_execution_errors
        self._disconnected_since: datetime | None = None
        self._consecutive_execution_errors = 0
        self._alerted_disconnect = False

    @property
    def disconnected_since(self) -> datetime | None:
        return self._disconnected_since

    def check_connectivity(self) -> bool:
        """Probe the broker link. Returns whether it's currently up.
        Engages the kill switch if it has been down beyond the threshold."""
        session = self.session_factory()
        try:
            connected = self.execution.is_connected()
            now = datetime.now(UTC)

            if connected:
                if self._disconnected_since is not None:
                    outage = (now - self._disconnected_since).total_seconds()
                    log_decision(
                        session,
                        event_type="broker_reconnected",
                        agent_id="connectivity_watchdog",
                        payload={"outage_seconds": round(outage, 1)},
                    )
                    if self._alerted_disconnect:
                        send_push(
                            session,
                            title="Broker reconnected",
                            body=f"Link restored after {round(outage)}s. Kill switch stays engaged until you re-enable it.",
                            data={"type": "broker_reconnected"},
                        )
                self._disconnected_since = None
                self._alerted_disconnect = False
                return True

            if self._disconnected_since is None:
                self._disconnected_since = now
                log_decision(
                    session,
                    event_type="broker_disconnected",
                    agent_id="connectivity_watchdog",
                    payload={"detail": getattr(self.execution, "last_connect_error", None)},
                )
                return False

            outage = (now - self._disconnected_since).total_seconds()
            if outage >= self.max_disconnected_seconds and not is_engaged(session):
                engage(
                    session,
                    reason=f"broker_disconnected_for_{round(outage)}s",
                    triggered_by="connectivity_watchdog",
                )
                self._alerted_disconnect = True
            return False
        finally:
            session.close()

    def record_execution_result(self, *, ok: bool, detail: str | None = None) -> None:
        """Feed every order attempt's outcome in. A run of consecutive failures
        means the execution path is broken (bad symbol config, no trading
        permission, terminal wedged) — retrying forever would just log noise
        while the account sits exposed, so it halts instead."""
        if ok:
            self._consecutive_execution_errors = 0
            return

        self._consecutive_execution_errors += 1
        if self._consecutive_execution_errors < self.max_consecutive_execution_errors:
            return

        session = self.session_factory()
        try:
            if not is_engaged(session):
                engage(
                    session,
                    reason=f"{self._consecutive_execution_errors}_consecutive_execution_errors",
                    triggered_by="connectivity_watchdog",
                )
                log_decision(
                    session,
                    event_type="execution_error_breaker_tripped",
                    agent_id="connectivity_watchdog",
                    payload={"consecutive_errors": self._consecutive_execution_errors, "last_detail": detail},
                )
        finally:
            session.close()
