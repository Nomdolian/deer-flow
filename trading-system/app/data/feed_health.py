from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.data.schema import Candle
from app.db.models import FeedHealthRecord
from app.logging_utils import log_decision
from app.notifications.service import notify_feed_health_issue


def record_candle_seen(session: Session, candle: Candle) -> None:
    """Refresh feed health ONLY when the candle actually advanced. A caller that
    polls a dead feed and keeps re-fetching the same last-closed candle must
    still trip staleness — refreshing `last_seen_at` on every poll regardless
    of whether new data arrived would make the staleness check unable to ever
    fire while polling continues, which defeats its purpose (Phase 1, item 4)."""
    row = session.get(FeedHealthRecord, candle.instrument)
    now = datetime.now(UTC)

    if row is None:
        session.add(FeedHealthRecord(instrument=candle.instrument, last_candle_time=candle.time, last_seen_at=now, is_stale=False))
        session.commit()
        return

    stored_time = row.last_candle_time.replace(tzinfo=UTC) if row.last_candle_time and row.last_candle_time.tzinfo is None else row.last_candle_time
    if stored_time is not None and candle.time <= stored_time:
        return  # no new data — do not refresh last_seen_at

    was_stale = row.is_stale
    row.last_candle_time = candle.time
    row.last_seen_at = now
    row.is_stale = False
    session.commit()
    if was_stale:
        log_decision(
            session,
            event_type="feed_recovered",
            agent_id="feed_health_monitor",
            instrument=candle.instrument,
            payload={"last_candle_time": candle.time.isoformat()},
        )


def is_stale(session: Session, instrument: str, *, stale_after_seconds: int | None = None) -> bool:
    """If an instrument's data hasn't updated within the threshold, it must be
    excluded from new signal generation until the feed recovers (Phase 1, item 4)."""
    threshold = stale_after_seconds if stale_after_seconds is not None else settings.feed_stale_seconds
    row = session.get(FeedHealthRecord, instrument)
    if row is None or row.last_seen_at is None:
        return True  # no data at all == fail closed == stale

    age = (datetime.now(UTC) - row.last_seen_at.replace(tzinfo=UTC)).total_seconds()
    stale_now = age > threshold

    if stale_now and not row.is_stale:
        row.is_stale = True
        session.commit()
        log_decision(
            session,
            event_type="feed_stale",
            agent_id="feed_health_monitor",
            instrument=instrument,
            payload={"age_seconds": age, "threshold_seconds": threshold},
        )
        notify_feed_health_issue(session, instrument=instrument)
    return stale_now
