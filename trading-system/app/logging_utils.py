import json
import logging
import sys
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.models import DecisionLog

_stdout_logger = logging.getLogger("trading_system")
_stdout_logger.setLevel(logging.INFO)
if not _stdout_logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _stdout_logger.addHandler(handler)


def log_decision(
    session: Session,
    *,
    event_type: str,
    agent_id: str,
    payload: dict,
    instrument: str | None = None,
    commit: bool = True,
) -> DecisionLog:
    """Persist a structured decision-log entry. This is the durable record required
    by Phase 0: every signal/risk/order event, timestamped, with full reasoning
    payload, written before the fact.

    Also mirrors to stdout as JSON so container log aggregation captures it even
    if the DB write is delayed.
    """
    record = DecisionLog(event_type=event_type, agent_id=agent_id, instrument=instrument, payload=payload)
    session.add(record)
    if commit:
        session.commit()
        session.refresh(record)

    _stdout_logger.info(
        json.dumps(
            {
                "ts": datetime.now(UTC).isoformat(),
                "event_type": event_type,
                "agent_id": agent_id,
                "instrument": instrument,
                "payload": payload,
            },
            default=str,
        )
    )
    return record
