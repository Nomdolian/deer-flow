"""The things the wizard's buttons actually do.

Each function is the button's whole job, returns a plain dict the page can
render, and never raises — a setup tool that shows a stack trace has failed at
the one thing it exists for.
"""

import base64
import os
import re
import secrets
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Keys the wizard owns in .env. Anything else in the file is left untouched, so
# hand-edits and future settings survive a wizard write.
_MANAGED_KEYS = (
    "DATABASE_URL",
    "MT5_LOGIN",
    "MT5_PASSWORD",
    "MT5_SERVER",
    "MT5_PATH",
    "API_AUTH_SECRET",
    "API_TOTP_SECRET",
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def env_path() -> Path:
    return project_root() / ".env"


# ---------------------------------------------------------------- .env writing


def _parse_env(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def read_env() -> dict[str, str]:
    path = env_path()
    return _parse_env(path.read_text()) if path.exists() else {}


def generate_secrets() -> dict:
    """Fresh API key and TOTP secret. Generated server-side rather than in the
    browser so they come from the OS CSPRNG."""
    return {
        "api_auth_secret": secrets.token_hex(32),
        "api_totp_secret": base64.b32encode(secrets.token_bytes(10)).decode(),
    }


def write_env(updates: dict[str, str]) -> dict:
    """Merge values into .env, preserving comments, ordering and unmanaged keys.

    Rewrites lines in place where the key already exists so the file stays
    readable, and appends anything new at the end.
    """
    path = env_path()
    example = project_root() / ".env.example"

    if path.exists():
        original = path.read_text()
    elif example.exists():
        # Start from the annotated example so the file keeps its explanations.
        original = example.read_text()
    else:
        original = ""

    clean = {k: v for k, v in updates.items() if k in _MANAGED_KEYS and v is not None}

    lines = original.splitlines()
    seen = set()
    for i, line in enumerate(lines):
        match = re.match(r"^([A-Z0-9_]+)\s*=", line.strip())
        if not match:
            continue
        key = match.group(1)
        if key in clean:
            lines[i] = f"{key}={clean[key]}"
            seen.add(key)

    for key, value in clean.items():
        if key not in seen:
            lines.append(f"{key}={value}")

    path.write_text("\n".join(lines).rstrip() + "\n")

    # pydantic-settings read .env once at import; refresh the live object so the
    # very next check sees what was just written instead of the old values.
    _refresh_settings()
    return {"ok": True, "path": str(path), "keys_written": sorted(clean)}


def _refresh_settings() -> None:
    from app.config import Settings, settings

    fresh = Settings()
    for field in Settings.model_fields:
        setattr(settings, field, getattr(fresh, field))


# ---------------------------------------------------------------- database


def create_tables() -> dict:
    try:
        from app.db.base import init_db

        init_db()
        return {"ok": True, "message": "Tables created."}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"Could not create tables: {exc}"}


# ---------------------------------------------------------------- broker


@dataclass
class SymbolReport:
    instrument: str
    tradeable: bool
    bid: float | None
    ask: float | None
    contract_size: float
    volume_min: float
    volume_step: float
    digits: int
    min_stop: float
    lots_at_risk: float | None
    sizing_note: str

    def as_dict(self) -> dict:
        return self.__dict__.copy()


_CRYPTO_HINTS = ("BTC", "ETH", "XRP", "DOGE", "SOL", "LTC", "BCH", "ADA", "BITCOIN", "ETHER")
_FOREX_HINTS = ("EUR", "USD", "GBP", "JPY", "AUD", "NZD", "CHF", "CAD")


def _guess_asset_class(name: str) -> str:
    upper = name.upper()
    if any(h in upper for h in _CRYPTO_HINTS):
        return "crypto_major"
    if "XAU" in upper or "GOLD" in upper or "XAG" in upper or "SILVER" in upper:
        return "metals"
    if sum(1 for h in _FOREX_HINTS if h in upper) >= 2:
        return "forex"
    return "indices"


def discover_symbols(search: str = "", limit: int = 60) -> dict:
    """List symbols the broker offers, filtered by a substring.

    Symbol names are broker-specific — BTCUSD at one broker is BTCUSD.a or
    Bitcoin at another — and typing the wrong one means the system fails closed
    and silently never trades it. Letting the operator pick from the broker's
    own list removes that whole category of mistake.
    """
    try:
        try:
            import MetaTrader5 as mt5
        except ImportError:
            # A setup tool has to say what to do, not what went wrong internally.
            return {
                "ok": False,
                "message": "The MetaTrader5 package isn't installed. It only works on Windows — "
                "re-run setup.bat there.",
                "symbols": [],
            }

        from app.execution.mt5_adapter import MT5Adapter

        adapter = MT5Adapter(mt5_module=mt5)
        if not adapter.ensure_connected():
            return {"ok": False, "message": f"MT5 not reachable: {adapter.last_connect_error}", "symbols": []}

        needle = search.strip().upper()
        names = [s.name for s in (mt5.symbols_get() or [])]
        if needle:
            names = [n for n in names if needle in n.upper()]
        names = sorted(names)[:limit]

        return {
            "ok": True,
            "total": len(names),
            "symbols": [{"instrument": n, "suggested_asset_class": _guess_asset_class(n)} for n in names],
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}", "symbols": []}


def inspect_symbols(instruments: list[str]) -> dict:
    """Contract terms plus what one authorized risk works out to in lots.

    This is the number that decides whether an instrument can trade on this
    account at all, so the wizard shows it before anything is committed.
    """
    try:
        import MetaTrader5 as mt5

        from app.config import settings
        from app.execution.mt5_adapter import MT5Adapter

        adapter = MT5Adapter(mt5_module=mt5)
        if not adapter.ensure_connected():
            return {"ok": False, "message": f"MT5 not reachable: {adapter.last_connect_error}", "symbols": []}

        account = mt5.account_info()
        equity = float(getattr(account, "equity", 0.0)) if account is not None else 0.0
        risk_amount = equity * settings.risk_per_trade_pct

        reports = []
        for instrument in instruments:
            spec = adapter.get_symbol_spec(instrument)
            if spec is None:
                reports.append(
                    {
                        "instrument": instrument,
                        "tradeable": False,
                        "sizing_note": "not found at this broker — check the exact name",
                        "lots_at_risk": None,
                    }
                )
                continue
            tick = mt5.symbol_info_tick(instrument)
            lots = None
            note = ""
            if tick is not None and risk_amount > 0:
                mid = (tick.bid + tick.ask) / 2
                # A 1% stop is only a yardstick for this preview; real signals
                # set their own, and a tighter stop needs a LARGER position.
                lots, error = adapter.resolve_volume(instrument, risk_amount / (mid * 0.01))
                note = error or f"about {lots:g} lots at {settings.risk_per_trade_pct:.2%} risk with a 1% stop"
            reports.append(
                SymbolReport(
                    instrument=instrument,
                    tradeable=adapter.is_tradeable(instrument),
                    bid=getattr(tick, "bid", None),
                    ask=getattr(tick, "ask", None),
                    contract_size=spec.contract_size,
                    volume_min=spec.volume_min,
                    volume_step=spec.volume_step,
                    digits=spec.digits,
                    min_stop=spec.min_stop_distance(),
                    lots_at_risk=lots,
                    sizing_note=note,
                ).as_dict()
            )
        return {"ok": True, "equity": equity, "risk_amount": risk_amount, "symbols": reports}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}", "symbols": []}


def save_watchlist(instruments: list[dict]) -> dict:
    """Replace the watchlist with exactly what the operator picked."""
    try:
        from app.db.base import SessionLocal
        from app.db.models import AssetClass
        from app.watchlist.service import add_instrument, list_watchlist, remove_instrument

        session = SessionLocal()
        try:
            wanted = {i["instrument"] for i in instruments}
            for existing in list_watchlist(session):
                if existing.instrument not in wanted:
                    remove_instrument(session, existing.instrument)
            for item in instruments:
                add_instrument(
                    session,
                    instrument=item["instrument"],
                    asset_class=AssetClass(item.get("asset_class", "forex")),
                    timeframe=item.get("timeframe", "M15"),
                    data_source="mt5",
                    enabled=bool(item.get("enabled", True)),
                )
            names = [r.instrument for r in list_watchlist(session)]
        finally:
            session.close()
        return {"ok": True, "instruments": names}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------- processes

# name -> the module to run. The API binds 0.0.0.0 because the phone needs it;
# the wizard itself never does (see server.py).
MANAGED_PROCESSES = {
    "api": [sys.executable, "-m", "uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"],
    "trader": [sys.executable, "-u", "-m", "scripts.run_mt5_live", "--poll-seconds", "30"],
    "scheduler": [sys.executable, "-u", "-m", "scripts.run_scheduler"],
}

PROCESS_LABELS = {
    "api": "API — what the phone app talks to",
    "trader": "Trading loop — places and manages orders",
    "scheduler": "Learning loop — expectancy stats and auto down-weighting",
}

_running: dict[str, subprocess.Popen] = {}
_log_paths: dict[str, Path] = {}


def logs_dir() -> Path:
    path = project_root() / "logs"
    path.mkdir(exist_ok=True)
    return path


def start_process(name: str) -> dict:
    if name not in MANAGED_PROCESSES:
        return {"ok": False, "message": f"unknown process {name!r}"}
    existing = _running.get(name)
    if existing is not None and existing.poll() is None:
        return {"ok": True, "message": "already running", "pid": existing.pid}

    log_file = logs_dir() / f"{name}.log"
    try:
        handle = log_file.open("a", encoding="utf-8", errors="replace")
        # New process group so stopping one doesn't signal the wizard itself.
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        # Fixed command list, no shell: `name` is validated against
        # MANAGED_PROCESSES above, so nothing user-supplied reaches argv.
        proc = subprocess.Popen(
            MANAGED_PROCESSES[name],
            cwd=str(project_root()),
            stdout=handle,
            stderr=subprocess.STDOUT,
            **kwargs,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"could not start: {exc}"}

    _running[name] = proc
    _log_paths[name] = log_file
    return {"ok": True, "message": "started", "pid": proc.pid, "log": str(log_file)}


def stop_process(name: str) -> dict:
    proc = _running.get(name)
    if proc is None or proc.poll() is not None:
        return {"ok": True, "message": "not running"}
    try:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"could not stop: {exc}"}
    return {"ok": True, "message": "stopped"}


def process_status() -> dict:
    out = {}
    for name in MANAGED_PROCESSES:
        proc = _running.get(name)
        alive = proc is not None and proc.poll() is None
        out[name] = {
            "label": PROCESS_LABELS[name],
            "running": alive,
            "pid": proc.pid if alive else None,
            "exit_code": None if proc is None or alive else proc.returncode,
            "log": str(_log_paths.get(name, logs_dir() / f"{name}.log")),
        }
    return out


def read_log(name: str, lines: int = 40) -> dict:
    if name not in MANAGED_PROCESSES:
        return {"ok": False, "message": f"unknown process {name!r}", "lines": []}
    path = _log_paths.get(name, logs_dir() / f"{name}.log")
    if not path.exists():
        return {"ok": True, "lines": [], "message": "no output yet"}
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc), "lines": []}
    return {"ok": True, "lines": content[-lines:]}


def stop_all() -> None:
    for name in list(_running):
        stop_process(name)


# ---------------------------------------------------------------- order test


def verify_trade(symbol: str) -> dict:
    """Run the demo round-trip: place one minimum-size order and close it.

    Shelled out to the real script rather than reimplemented, so the wizard
    can't drift from the command-line path — including its refusal to touch a
    funded account.
    """
    try:
        # check=False on purpose: a non-zero exit is the interesting case
        # here (the broker rejected the order), and the output is what the
        # operator needs to see, not an exception.
        proc = subprocess.run(  # noqa: PLW1510
            [sys.executable, "-u", "-m", "scripts.verify_mt5_trade", "--symbol", symbol, "--hold-seconds", "1"],
            cwd=str(project_root()),
            capture_output=True,
            text=True,
            timeout=120,
            input="",  # never let it block on the --allow-live confirmation prompt
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "The test did not finish within 2 minutes. Check the MT5 terminal."}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "output": f"{type(exc).__name__}: {exc}"}

    output = (proc.stdout or "") + (proc.stderr or "")
    return {"ok": proc.returncode == 0, "exit_code": proc.returncode, "output": output.strip()}


SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9._#\-]{1,32}$")


def valid_symbol(symbol: str) -> bool:
    """Broker symbols are alphanumerics plus a few separators. Enforced because
    the value reaches a subprocess argument list."""
    return bool(SYMBOL_PATTERN.match(symbol or ""))
