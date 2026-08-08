import pytest

from app.db.models import AssetClass, WatchlistInstrument
from app.watchlist import service


def test_add_instrument_creates_row(db_session):
    row = service.add_instrument(db_session, instrument="EURUSD", asset_class=AssetClass.forex)
    assert row.instrument == "EURUSD"
    assert row.enabled is True
    assert db_session.query(WatchlistInstrument).count() == 1


def test_add_instrument_is_idempotent_on_instrument_name(db_session):
    service.add_instrument(db_session, instrument="EURUSD", asset_class=AssetClass.forex, timeframe="D1")
    service.add_instrument(db_session, instrument="EURUSD", asset_class=AssetClass.forex, timeframe="H1")
    assert db_session.query(WatchlistInstrument).count() == 1
    row = db_session.query(WatchlistInstrument).one()
    assert row.timeframe == "H1"  # second call updates rather than duplicating


def test_list_watchlist_enabled_only_filters(db_session):
    service.add_instrument(db_session, instrument="EURUSD", asset_class=AssetClass.forex, enabled=True)
    service.add_instrument(db_session, instrument="GBPUSD", asset_class=AssetClass.forex, enabled=False)

    assert len(service.list_watchlist(db_session)) == 2
    enabled = service.list_watchlist(db_session, enabled_only=True)
    assert len(enabled) == 1
    assert enabled[0].instrument == "EURUSD"


def test_set_enabled_toggles_flag(db_session):
    service.add_instrument(db_session, instrument="EURUSD", asset_class=AssetClass.forex, enabled=True)
    service.set_enabled(db_session, "EURUSD", False)
    row = db_session.query(WatchlistInstrument).filter_by(instrument="EURUSD").one()
    assert row.enabled is False


def test_set_enabled_unknown_instrument_raises(db_session):
    with pytest.raises(ValueError, match="not on the watchlist"):
        service.set_enabled(db_session, "NOPE", True)


def test_remove_instrument_deletes_row(db_session):
    service.add_instrument(db_session, instrument="EURUSD", asset_class=AssetClass.forex)
    service.remove_instrument(db_session, "EURUSD")
    assert db_session.query(WatchlistInstrument).count() == 0
