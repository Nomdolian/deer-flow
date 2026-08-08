from sqlalchemy.orm import Session

from app.db.models import AssetClass, WatchlistInstrument
from app.logging_utils import log_decision


def list_watchlist(session: Session, *, enabled_only: bool = False) -> list[WatchlistInstrument]:
    query = session.query(WatchlistInstrument)
    if enabled_only:
        query = query.filter(WatchlistInstrument.enabled.is_(True))
    return query.order_by(WatchlistInstrument.instrument).all()


def add_instrument(
    session: Session,
    *,
    instrument: str,
    asset_class: AssetClass,
    timeframe: str = "D1",
    data_source: str = "alphavantage",
    enabled: bool = True,
) -> WatchlistInstrument:
    existing = session.query(WatchlistInstrument).filter_by(instrument=instrument).one_or_none()
    if existing is not None:
        existing.asset_class = asset_class
        existing.timeframe = timeframe
        existing.data_source = data_source
        existing.enabled = enabled
        session.commit()
        session.refresh(existing)
        return existing

    row = WatchlistInstrument(
        instrument=instrument,
        asset_class=asset_class,
        timeframe=timeframe,
        data_source=data_source,
        enabled=enabled,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    log_decision(
        session,
        event_type="watchlist_instrument_added",
        agent_id="watchlist",
        instrument=instrument,
        payload={"asset_class": asset_class.value, "timeframe": timeframe, "data_source": data_source},
    )
    return row


def set_enabled(session: Session, instrument: str, enabled: bool) -> WatchlistInstrument:
    row = session.query(WatchlistInstrument).filter_by(instrument=instrument).one_or_none()
    if row is None:
        raise ValueError(f"{instrument!r} is not on the watchlist — add it first")
    row.enabled = enabled
    session.commit()
    log_decision(
        session,
        event_type="watchlist_instrument_enabled" if enabled else "watchlist_instrument_disabled",
        agent_id="watchlist",
        instrument=instrument,
        payload={},
    )
    return row


def remove_instrument(session: Session, instrument: str) -> None:
    session.query(WatchlistInstrument).filter_by(instrument=instrument).delete()
    session.commit()
    log_decision(session, event_type="watchlist_instrument_removed", agent_id="watchlist", instrument=instrument, payload={})
