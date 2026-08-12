"""CORS on the trading API.

The API can halt or resume live trading, so "any website may call this" is never
an acceptable default. Native iOS/Android apps aren't subject to CORS at all —
only the web build of the mobile app needs it — so this stays off unless the
operator names exact origins.
"""

import importlib

import pytest

from app.config import settings


def _app_with_origins(monkeypatch, value: str):
    """Rebuild the app: the middleware is attached at import time."""
    monkeypatch.setattr(settings, "api_cors_origins", value)
    from app.api import main

    return importlib.reload(main).app


@pytest.fixture(autouse=True)
def _restore_app():
    yield
    # Leave the module in its default state for other tests.
    from app.api import main

    importlib.reload(main)


def test_cors_is_off_by_default(monkeypatch):
    from fastapi.testclient import TestClient

    client = TestClient(_app_with_origins(monkeypatch, ""))
    response = client.get("/health", headers={"Origin": "http://localhost:8081"})
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


def test_named_origins_are_allowed(monkeypatch):
    from fastapi.testclient import TestClient

    client = TestClient(_app_with_origins(monkeypatch, "http://localhost:8081,http://127.0.0.1:8099"))
    response = client.get("/health", headers={"Origin": "http://127.0.0.1:8099"})
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:8099"


def test_an_unlisted_origin_is_not_allowed(monkeypatch):
    from fastapi.testclient import TestClient

    client = TestClient(_app_with_origins(monkeypatch, "http://localhost:8081"))
    response = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


def test_a_wildcard_is_refused_rather_than_honoured(monkeypatch):
    """Someone will try `API_CORS_ORIGINS=*`. It must not silently open the
    trading API to every site the operator ever visits."""
    from fastapi.testclient import TestClient

    client = TestClient(_app_with_origins(monkeypatch, "*"))
    response = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


def test_a_wildcard_mixed_with_real_origins_drops_only_the_wildcard(monkeypatch):
    from fastapi.testclient import TestClient

    client = TestClient(_app_with_origins(monkeypatch, "*,http://127.0.0.1:8099"))
    allowed = client.get("/health", headers={"Origin": "http://127.0.0.1:8099"})
    assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:8099"
    denied = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in denied.headers}
