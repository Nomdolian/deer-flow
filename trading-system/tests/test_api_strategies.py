"""API-level tests for the strategy resume endpoint.

This is the only way an operator can clear a latched pause, so it's worth
covering at the HTTP boundary rather than only at the service layer: an
unreachable or mis-authenticated endpoint leaves the system permanently
halted just as surely as a missing resume function would.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.config import settings
from app.db.base import get_session
from app.db.models import AssetClass, StrategyVersion
from app.risk.manager import record_trade_result

API_KEY = "test-api-key-long-enough-to-not-be-the-placeholder"


@pytest.fixture
def client(db_session, monkeypatch):
    monkeypatch.setattr(settings, "api_auth_secret", API_KEY)
    # TOTP not configured -> require_totp passes through, but the header is
    # still declared, so requests must send one. Mirrors an operator who has
    # opted out of 2FA.
    monkeypatch.setattr(settings, "api_totp_secret", "")
    app.dependency_overrides[get_session] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _paused_strategy(session, strategy_id="smc_ict_structure", version=1):
    session.add(StrategyVersion(strategy_id=strategy_id, version=version, asset_class=AssetClass.forex))
    session.commit()
    for _ in range(4):
        record_trade_result(session, strategy_id, version, is_loss=True, breaker_threshold=4)
    return session.query(StrategyVersion).filter_by(strategy_id=strategy_id, version=version).one()


def test_resume_clears_the_pause(client, db_session):
    strategy = _paused_strategy(db_session)
    assert strategy.is_paused

    response = client.post(
        f"/strategies/{strategy.strategy_id}/{strategy.version}/resume",
        headers={"x-api-key": API_KEY, "x-totp-code": "000000"},
    )
    assert response.status_code == 200
    assert response.json()["is_paused"] is False

    db_session.refresh(strategy)
    assert not strategy.is_paused
    assert strategy.consecutive_losses == 0


def test_resume_requires_the_api_key(client, db_session):
    strategy = _paused_strategy(db_session)
    response = client.post(
        f"/strategies/{strategy.strategy_id}/{strategy.version}/resume",
        headers={"x-api-key": "wrong", "x-totp-code": "000000"},
    )
    assert response.status_code == 401

    db_session.refresh(strategy)
    assert strategy.is_paused  # still halted


def test_resume_unknown_strategy_is_a_404(client):
    response = client.post(
        "/strategies/nope/1/resume",
        headers={"x-api-key": API_KEY, "x-totp-code": "000000"},
    )
    assert response.status_code == 404


def test_listing_strategies_surfaces_the_paused_flag(client, db_session):
    _paused_strategy(db_session)
    response = client.get("/strategies", headers={"x-api-key": API_KEY})
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["is_paused"] is True
    assert rows[0]["consecutive_losses"] == 4
