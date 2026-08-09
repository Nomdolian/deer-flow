"""The setup wizard.

Two things carry real weight here. First, it writes `.env` — a bug that wipes a
working broker password or a hand-added setting is not recoverable from the UI.
Second, it accepts a broker password and spawns processes, so its access control
has to hold: no token, no service, and nothing off-machine.
"""

import pytest
from fastapi.testclient import TestClient

from app.setup_wizard import actions, status
from app.setup_wizard.server import TOKEN, app


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Point the wizard at a throwaway project directory."""
    monkeypatch.setattr(actions, "project_root", lambda: tmp_path)
    monkeypatch.setattr(status, "_project_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def client():
    # TestClient reports its peer as "testclient" by default, which the
    # localhost-only middleware correctly rejects — so say we're local.
    return TestClient(app, client=("127.0.0.1", 12345))


def auth(path: str) -> str:
    joiner = "&" if "?" in path else "?"
    return f"{path}{joiner}token={TOKEN}"


# ---------------------------------------------------------------- access control


def test_every_route_refuses_without_the_token(client):
    for method, path in (
        ("get", "/"),
        ("get", "/api/status"),
        ("post", "/api/generate-secrets"),
        ("post", "/api/create-tables"),
        ("get", "/api/symbols"),
        ("post", "/api/process/api/start"),
    ):
        response = getattr(client, method)(path)
        assert response.status_code == 403, f"{method} {path} was reachable without a token"


def test_a_wrong_token_is_refused(client):
    assert client.get("/api/status?token=not-the-token").status_code == 403


def test_the_right_token_is_accepted(client):
    assert client.get(auth("/api/status")).status_code == 200


def test_the_page_never_leaks_the_broker_password(client, project):
    actions.write_env({"MT5_PASSWORD": "hunter2", "MT5_LOGIN": "555", "API_AUTH_SECRET": "x" * 40})
    body = client.get(auth("/api/status")).json()
    assert "hunter2" not in str(body)
    # The login and server are echoed back so the form can be repopulated; the
    # password deliberately never is.
    assert body["env"]["mt5_login"] == "555"
    assert "mt5_password" not in body["env"]


def test_a_non_local_client_is_refused():
    """The wizard binds 127.0.0.1, so this shouldn't be reachable at all — the
    middleware is the second line of defence if it ever gets served wider."""
    remote = TestClient(app, client=("203.0.113.9", 1234))
    response = remote.get(auth("/api/status"))
    assert response.status_code == 403
    assert "only available on this computer" in response.json()["detail"]


def test_a_spoofed_forwarding_header_does_not_grant_access():
    """Access is decided by the actual peer address, not a header a caller can
    set — otherwise the localhost rule would be trivially bypassable."""
    remote = TestClient(app, client=("203.0.113.9", 1234))
    response = remote.get(auth("/api/status"), headers={"x-forwarded-for": "127.0.0.1"})
    assert response.status_code == 403


# ---------------------------------------------------------------- .env writing


def test_writing_env_preserves_comments_and_unmanaged_keys(project):
    (project / ".env").write_text(
        "# my notes\n"
        "DATABASE_URL=sqlite:///./old.db\n"
        "ANTHROPIC_API_KEY=keep-me\n"
        "RISK_PER_TRADE_PCT=0.005\n"
    )

    actions.write_env({"DATABASE_URL": "sqlite:///./trading.db", "MT5_LOGIN": "42"})

    text = (project / ".env").read_text()
    assert "# my notes" in text
    assert "ANTHROPIC_API_KEY=keep-me" in text
    assert "RISK_PER_TRADE_PCT=0.005" in text
    assert "DATABASE_URL=sqlite:///./trading.db" in text
    assert "sqlite:///./old.db" not in text
    assert "MT5_LOGIN=42" in text


def test_writing_env_ignores_keys_it_does_not_own(project):
    """A stray key from a crafted request must not be able to set arbitrary
    configuration — only the fields the wizard's form actually offers."""
    actions.write_env({"MT5_LOGIN": "42", "ANTHROPIC_API_KEY": "injected", "SOMETHING_ELSE": "no"})

    text = (project / ".env").read_text()
    assert "MT5_LOGIN=42" in text
    assert "injected" not in text
    assert "SOMETHING_ELSE" not in text


def test_a_blank_password_keeps_the_saved_one(client, project):
    actions.write_env({"MT5_PASSWORD": "original-pw"})

    client.post(auth("/api/save-connection"), json={"mt5_login": "99", "mt5_server": "S", "mt5_password": ""})

    assert actions.read_env()["MT5_PASSWORD"] == "original-pw"
    assert actions.read_env()["MT5_LOGIN"] == "99"


def test_generated_secrets_are_long_enough_and_saved(client, project):
    body = client.post(auth("/api/generate-secrets")).json()

    assert len(body["api_auth_secret"]) >= 64  # 32 bytes as hex
    assert len(body["api_totp_secret"]) == 16  # 10 bytes as base32
    saved = actions.read_env()
    assert saved["API_AUTH_SECRET"] == body["api_auth_secret"]
    assert saved["API_TOTP_SECRET"] == body["api_totp_secret"]


def test_two_generate_calls_do_not_repeat_a_secret(client, project):
    first = client.post(auth("/api/generate-secrets")).json()["api_auth_secret"]
    second = client.post(auth("/api/generate-secrets")).json()["api_auth_secret"]
    assert first != second


# ---------------------------------------------------------------- input guards


@pytest.mark.parametrize(
    "symbol",
    ["EURUSD", "BTCUSD.a", "XAU_USD", "US30#", "EURUSD-raw"],
)
def test_real_broker_symbol_shapes_are_accepted(symbol):
    assert actions.valid_symbol(symbol)


@pytest.mark.parametrize(
    "symbol",
    ["", "../../etc/passwd", "EUR USD", "EURUSD; rm -rf /", "$(whoami)", "a" * 33],
)
def test_symbols_that_could_reach_a_subprocess_are_rejected(symbol):
    assert not actions.valid_symbol(symbol)


def test_verify_trade_refuses_a_bogus_symbol_without_running_anything(client, monkeypatch):
    called = []
    monkeypatch.setattr(actions, "verify_trade", lambda s: called.append(s))

    body = client.post(auth("/api/verify-trade"), json={"symbol": "../../etc/passwd"}).json()

    assert body["ok"] is False
    assert "not a valid symbol" in body["output"]
    assert called == []


def test_unknown_process_names_are_refused(client):
    body = client.post(auth("/api/process/rm-rf/start")).json()
    assert body["ok"] is False
    assert "unknown process" in body["message"]


def test_an_invalid_process_command_is_a_400(client):
    assert client.post(auth("/api/process/api/restart")).status_code == 400


# ---------------------------------------------------------------- status checks


def test_status_reports_every_check_even_when_one_explodes(monkeypatch):
    def boom() -> status.Check:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(status, "ORDERED_CHECKS", (status.check_python, boom))
    checks = status.collect()

    assert len(checks) == 2
    assert checks[0]["state"] == "ok"
    assert checks[1]["state"] == "fail"
    assert "kaboom" in checks[1]["detail"]


def test_readiness_needs_the_blocking_checks_but_tolerates_warnings():
    def build(states: dict) -> list[dict]:
        return [{"key": k, "state": v} for k, v in states.items()]

    blocking = ["python", "mt5_package", "env_file", "secrets", "mt5_credentials", "database", "terminal", "watchlist"]

    assert status.is_ready_to_trade(build(dict.fromkeys(blocking, "ok")))
    # A live account or a missing 2FA secret warns; that's the operator's call.
    assert status.is_ready_to_trade(build(dict.fromkeys(blocking, "warn")))
    # A missing watchlist is not.
    states = dict.fromkeys(blocking, "ok")
    states["watchlist"] = "todo"
    assert not status.is_ready_to_trade(build(states))


def test_missing_env_reads_as_a_todo_not_a_failure(project):
    check = status.check_env_file()
    assert check.state == "todo"
    assert check.fix


def test_symbol_discovery_without_mt5_explains_what_to_do(monkeypatch):
    """On a machine with no MetaTrader5 package the wizard must say something
    actionable rather than surfacing ModuleNotFoundError."""
    import builtins

    real_import = builtins.__import__

    def no_mt5(name, *args, **kwargs):
        if name == "MetaTrader5":
            raise ImportError("no module named MetaTrader5")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_mt5)
    result = actions.discover_symbols("EUR")

    assert result["ok"] is False
    assert "Windows" in result["message"]
    assert "ModuleNotFoundError" not in result["message"]
