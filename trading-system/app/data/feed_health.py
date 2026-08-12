"""Feed staleness tracking.

A dead feed that keeps answering with the same last candle is more dangerous than
one that errors: the orchestrator would happily size and place orders off a price
that stopped updating an hour ago. So staleness is measured by **candle
progression**, not by whether a poll succeeded.

The distinction that matters: re-polling and receiving the *same* candle is
evidence the feed is stuck, and must not refresh the clock.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.data.schema import Candle
from app.db.models import FeedHealthRecord


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; normalize before comparing."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def record_candle_seen(session: Session, candle: Candle) -> FeedHealthRecord:
    """Register that `candle` arrived for its instrument.

    Only advances the staleness clock when the candle is genuinely newer than the
    last one recorded. A repeat of the same (or an older) candle leaves the row
    completely untouched — note that FeedHealthRecord.last_seen_at carries an
    `onupdate` default, so writing *any* field would silently reset the clock and
    mask a stuck feed.
    """
    record = session.get(FeedHealthRecord, candle.instrument)
    candle_time = _as_utc(candle.time)

    if record is None:
        record = FeedHealthRecord(
            instrument=candle.instrument,
            last_candle_time=candle_time,
            last_seen_at=datetime.now(UTC),
            is_stale=False,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return record

    known = _as_utc(record.last_candle_time)
    if known is not None and candle_time is not None and candle_time <= known:
        return record  # stuck or replayed feed: deliberately no write at all

    record.last_candle_time = candle_time
    record.last_seen_at = datetime.now(UTC)
    record.is_stale = False
    session.commit()
    session.refresh(record)
    return record


def is_stale(session: Session, instrument: str, stale_after_seconds: int | None = None) -> bool:
    """True when no fresh candle has arrived within the staleness window.

    Absence of data is treated as stale: never having seen a candle is not
    evidence that the feed is healthy.
    """
    threshold = settings.feed_stale_seconds if stale_after_seconds is None else stale_after_seconds
    record = session.get(FeedHealthRecord, instrument)
    if record is None:
        return True

    last_seen = _as_utc(record.last_seen_at)
    if last_seen is None:
        return True

    stale = (datetime.now(UTC) - last_seen).total_seconds() > threshold
    if stale != record.is_stale:
        record.is_stale = stale
        session.commit()
    return stale


def mark_stale(session: Session, instrument: str) -> None:
    """Flag a feed as stale explicitly, e.g. after a provider raised."""
    record = session.get(FeedHealthRecord, instrument)
    if record is not None and not record.is_stale:
        record.is_stale = True
        session.commit()
