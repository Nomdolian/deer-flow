from datetime import UTC, datetime, timedelta

from app.data.feed_health import is_stale, record_candle_seen
from app.db.models import FeedHealthRecord
from tests.conftest import make_candles


def test_repolling_the_same_candle_does_not_refresh_staleness_clock(db_session):
    candle = make_candles(1)[0]
    record_candle_seen(db_session, candle)

    row = db_session.get(FeedHealthRecord, candle.instrument)
    row.last_seen_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.commit()

    # Same candle re-polled (e.g. a dead feed still answering with stale data):
    # must NOT reset last_seen_at back to "now".
    record_candle_seen(db_session, candle)
    row = db_session.get(FeedHealthRecord, candle.instrument)
    assert row.last_seen_at.replace(tzinfo=UTC) < datetime.now(UTC) - timedelta(minutes=30)

    assert is_stale(db_session, candle.instrument, stale_after_seconds=60)


def test_new_candle_refreshes_and_clears_staleness(db_session):
    candle = make_candles(2)
    record_candle_seen(db_session, candle[0])
    row = db_session.get(FeedHealthRecord, candle[0].instrument)
    row.last_seen_at = datetime.now(UTC) - timedelta(hours=1)
    row.is_stale = True
    db_session.commit()

    record_candle_seen(db_session, candle[1])  # newer candle.time than stored
    assert not is_stale(db_session, candle[0].instrument, stale_after_seconds=60)


def test_no_data_at_all_is_stale_by_default(db_session):
    assert is_stale(db_session, "UNKNOWN") is True
