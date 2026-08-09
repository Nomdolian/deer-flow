"""/positions must return only what is open right now.

An order's status stays "filled" forever, so anything keyed off it reports
long-closed trades as live exposure. That is the one number on the dashboard
that has to be right — it's what tells the operator whether they're in the
market at all.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.config import settings
from app.db.base import get_session
from app.db.models import (
    AssetClass,
    Direction,
    OrderRecord,
    TradeJournalRecord,
)

API_KEY = "test-api-key-long-enough-to-not-be-the-placeholder"


@pytest.fixture
def client(db_session, monkeypatch):
    monkeypatch.setattr(settings, "api_auth_secret", API_KEY)
    app.dependency_overrides[get_session] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _trade(session, *, instrument: str, closed: bool) -> None:
    order = OrderRecord(
        client_order_id=f"coid-{instrument}-{closed}",
        instrument=instrument,
        direction=Direction.long,
        requested_size=1000.0,
        filled_price=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        status="filled",
        broker="paper",
    )
    session.add(order)
    session.flush()
    session.add(
        TradeJournalRecord(
            order_id=order.id,
            strategy_id="smc_ict_structure",
            strategy_version=1,
            instrument=instrument,
            asset_class=AssetClass.forex,
            direction=Direction.long,
            size=1000.0,
            entry_price=1.1000,
            stop_loss=1.0950,
            take_profit=1.1100,
            exit_price=1.1100 if closed else None,
            closed_at=datetime.now(UTC) if closed else None,
            outcome="win" if closed else None,
        )
    )
    session.commit()


def test_closed_trades_are_not_reported_as_open_positions(client, db_session):
    _trade(db_session, instrument="EURUSD", closed=True)
    _trade(db_session, instrument="GBPUSD", closed=True)

    response = client.get("/positions", headers={"x-api-key": API_KEY})
    assert response.status_code == 200
    assert response.json() == []  # flat, despite two orders sitting at status="filled"


def test_open_positions_are_returned_with_strategy_and_asset_class(client, db_session):
    _trade(db_session, instrument="EURUSD", closed=True)
    _trade(db_session, instrument="BTCUSD", closed=False)

    rows = client.get("/positions", headers={"x-api-key": API_KEY}).json()
    assert [r["instrument"] for r in rows] == ["BTCUSD"]
    assert rows[0]["strategy_id"] == "smc_ict_structure"
    assert rows[0]["asset_class"] == "forex"
    assert rows[0]["stop_loss"] == 1.0950
    assert rows[0]["take_profit"] == 1.1100


def test_positions_requires_the_api_key(client, db_session):
    _trade(db_session, instrument="EURUSD", closed=False)
    assert client.get("/positions", headers={"x-api-key": "wrong"}).status_code == 401
