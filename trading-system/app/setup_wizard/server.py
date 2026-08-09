"""Localhost-only setup wizard.

Replaces the PowerShell steps with a page of buttons. It handles the broker
password and can start processes, so it is deliberately locked down harder than
the trading API:

- binds 127.0.0.1 only, never 0.0.0.0 — unlike the trading API, which has to be
  reachable from the phone. Nothing about setup should be reachable from the
  network.
- every request needs a token minted at launch and carried in the URL, so
  another local process that guesses the port still can't drive it.
- exits when you close it. It is not a service, and leaving a
  password-accepting, process-spawning endpoint running is not a good default.
"""

import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.setup_wizard import actions, status

TOKEN = secrets.token_urlsafe(24)

app = FastAPI(title="Trading system setup", docs_url=None, redoc_url=None, openapi_url=None)


def _require_token(token: str) -> None:
    if not secrets.compare_digest(token, TOKEN):
        raise HTTPException(status_code=403, detail="Wrong or missing setup token. Use the link the setup file opened.")


@app.middleware("http")
async def _localhost_only(request: Request, call_next):
    """Belt and braces: even if something binds this wider than intended, a
    non-local client gets nothing."""
    client = request.client.host if request.client else ""
    if client not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse({"detail": "The setup page is only available on this computer."}, status_code=403)
    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
def page(token: str = Query("")) -> HTMLResponse:
    _require_token(token)
    html = (Path(__file__).parent / "page.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("__TOKEN__", TOKEN))


@app.get("/api/status")
def api_status(token: str = Query("")) -> dict:
    _require_token(token)
    checks = status.collect()
    return {
        "checks": checks,
        "ready": status.is_ready_to_trade(checks),
        "processes": actions.process_status(),
        "env": {
            # Never the password: the wizard writes it and never reads it back.
            "database_url": actions.read_env().get("DATABASE_URL", ""),
            "mt5_login": actions.read_env().get("MT5_LOGIN", ""),
            "mt5_server": actions.read_env().get("MT5_SERVER", ""),
            "has_api_secret": bool(actions.read_env().get("API_AUTH_SECRET", "")),
            "totp_secret": actions.read_env().get("API_TOTP_SECRET", ""),
        },
    }


@app.post("/api/generate-secrets")
def api_generate_secrets(token: str = Query("")) -> dict:
    _require_token(token)
    generated = actions.generate_secrets()
    actions.write_env(
        {
            "API_AUTH_SECRET": generated["api_auth_secret"],
            "API_TOTP_SECRET": generated["api_totp_secret"],
        }
    )
    return {"ok": True, **generated}


@app.post("/api/save-connection")
async def api_save_connection(request: Request, token: str = Query("")) -> dict:
    _require_token(token)
    body = await request.json()
    updates = {
        "DATABASE_URL": body.get("database_url") or "sqlite:///./trading.db",
        "MT5_LOGIN": (body.get("mt5_login") or "").strip(),
        "MT5_SERVER": (body.get("mt5_server") or "").strip(),
    }
    # An empty password field means "keep what's already saved", so re-saving
    # the form doesn't wipe a working password.
    password = body.get("mt5_password")
    if password:
        updates["MT5_PASSWORD"] = password
    path = (body.get("mt5_path") or "").strip()
    if path:
        updates["MT5_PATH"] = path
    return actions.write_env(updates)


@app.post("/api/create-tables")
def api_create_tables(token: str = Query("")) -> dict:
    _require_token(token)
    return actions.create_tables()


@app.get("/api/symbols")
def api_symbols(token: str = Query(""), search: str = Query("")) -> dict:
    _require_token(token)
    return actions.discover_symbols(search)


@app.post("/api/inspect-symbols")
async def api_inspect_symbols(request: Request, token: str = Query("")) -> dict:
    _require_token(token)
    body = await request.json()
    instruments = [s for s in body.get("instruments", []) if actions.valid_symbol(s)]
    return actions.inspect_symbols(instruments)


@app.post("/api/save-watchlist")
async def api_save_watchlist(request: Request, token: str = Query("")) -> dict:
    _require_token(token)
    body = await request.json()
    picked = [i for i in body.get("instruments", []) if actions.valid_symbol(i.get("instrument", ""))]
    return actions.save_watchlist(picked)


@app.post("/api/verify-trade")
async def api_verify_trade(request: Request, token: str = Query("")) -> dict:
    _require_token(token)
    body = await request.json()
    symbol = (body.get("symbol") or "").strip()
    if not actions.valid_symbol(symbol):
        return {"ok": False, "output": f"{symbol!r} is not a valid symbol name."}
    return actions.verify_trade(symbol)


@app.post("/api/process/{name}/{command}")
def api_process(name: str, command: str, token: str = Query("")) -> dict:
    _require_token(token)
    if command == "start":
        return actions.start_process(name)
    if command == "stop":
        return actions.stop_process(name)
    raise HTTPException(status_code=400, detail="command must be start or stop")


@app.get("/api/process/{name}/log")
def api_process_log(name: str, token: str = Query(""), lines: int = Query(40, ge=1, le=400)) -> dict:
    _require_token(token)
    return actions.read_log(name, lines)
