from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.models import KillSwitchState
from app.logging_utils import log_decision
from app.notifications.service import notify_kill_switch_triggered

_GLOBAL_ID = "global"


class KillSwitchEngaged(Exception):
    """Raised by callers on the execution path when the kill switch is on.
    Fail closed: catching and ignoring this exception is a bug, not a feature."""


def _get_or_create(session: Session) -> KillSwitchState:
    state = session.get(KillSwitchState, _GLOBAL_ID)
    if state is None:
        state = KillSwitchState(id=_GLOBAL_ID, engaged=False)
        session.add(state)
        session.commit()
        session.refresh(state)
    return state


def is_engaged(session: Session) -> bool:
    return _get_or_create(session).engaged


def get_state(session: Session) -> KillSwitchState:
    return _get_or_create(session)


def engage(session: Session, *, reason: str, triggered_by: str) -> KillSwitchState:
    """Halts all NEW order placement across every agent and asset class.
    Does NOT close existing positions — that is a separate, explicit action."""
    state = _get_or_create(session)
    state.engaged = True
    state.reason = reason
    state.triggered_by = triggered_by
    state.engaged_at = datetime.now(UTC)
    session.commit()
    log_decision(
        session,
        event_type="kill_switch_engaged",
        agent_id=triggered_by,
        payload={"reason": reason},
    )
    notify_kill_switch_triggered(session, reason=reason, triggered_by=triggered_by)
    return state


def disengage(session: Session, *, triggered_by: str) -> KillSwitchState:
    """Manual re-enable only. Automatic triggers never call this."""
    state = _get_or_create(session)
    state.engaged = False
    state.reason = None
    state.triggered_by = triggered_by
    session.commit()
    log_decision(
        session,
        event_type="kill_switch_disengaged",
        agent_id=triggered_by,
        payload={},
    )
    return state


def assert_not_engaged(session: Session) -> None:
    """Call this at the top of any code path that could lead to an order.
    Fail closed on kill switch, same as on missing data or an API error."""
    if is_engaged(session):
        state = _get_or_create(session)
        raise KillSwitchEngaged(state.reason or "kill switch engaged")
