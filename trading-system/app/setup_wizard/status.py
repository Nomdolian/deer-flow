"""Read-only inspection of how far setup has got.

Kept separate from actions.py so the wizard can re-render its checklist as often
as it likes without any risk of changing something. Every check answers with a
state plus a human sentence and, where it can, the thing to do next — the same
philosophy as scripts/doctor.py, which this deliberately mirrors rather than
replaces (the wizard is for people who don't want a terminal; doctor is for
people who do).
"""

import importlib.util
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path

PLACEHOLDER_SECRET = "change-me-to-a-long-random-value"


@dataclass
class Check:
    key: str
    label: str
    state: str  # "ok" | "warn" | "fail" | "todo"
    detail: str = ""
    fix: str = ""
    data: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "state": self.state,
            "detail": self.detail,
            "fix": self.fix,
            "data": self.data,
        }


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def check_python() -> Check:
    major, minor = sys.version_info[:2]
    if (major, minor) in ((3, 12), (3, 13)):
        return Check("python", "Python version", "ok", f"Python {major}.{minor}")
    if (major, minor) < (3, 12):
        return Check(
            "python", "Python version", "fail",
            f"Python {major}.{minor} is too old",
            "Install Python 3.12 or 3.13 from python.org and re-run the setup file.",
        )
    return Check(
        "python", "Python version", "warn",
        f"Python {major}.{minor} is newer than this has been tested against",
        "MetaTrader5 may not publish a wheel for it. 3.12 or 3.13 are the safe choices.",
    )


def check_mt5_package() -> Check:
    if platform.system() != "Windows":
        return Check(
            "mt5_package", "MetaTrader5 package", "warn",
            f"not Windows ({platform.system()}) — live MT5 trading is unavailable here",
            "MT5's Python API is Windows-only. Everything else in the system works on any OS.",
        )
    if importlib.util.find_spec("MetaTrader5") is None:
        return Check(
            "mt5_package", "MetaTrader5 package", "fail",
            "not installed",
            'Re-run setup.bat, or install manually: uv pip install -e ".[mt5]"',
        )
    return Check("mt5_package", "MetaTrader5 package", "ok", "installed")


def check_env_file() -> Check:
    path = _project_root() / ".env"
    if not path.exists():
        return Check(
            "env_file", "Configuration file", "todo",
            "no .env yet",
            "Fill in the connection form below — the wizard writes the file for you.",
        )
    return Check("env_file", "Configuration file", "ok", f"{path.name} exists", data={"path": str(path)})


def check_secrets() -> Check:
    from app.config import settings

    missing = []
    if not settings.api_auth_secret or settings.api_auth_secret == PLACEHOLDER_SECRET:
        missing.append("API key")
    if len(settings.api_auth_secret or "") < 32:
        missing.append("API key is too short")
    if not settings.api_totp_secret:
        # Not fatal: 2FA is optional, but it gates re-enabling trading after an
        # automatic halt, so running without it is worth flagging.
        return Check(
            "secrets", "App secrets", "warn",
            "API key set, but no two-factor secret",
            "Without it, anything holding the API key can re-enable trading after an automatic halt.",
        )
    if missing:
        return Check("secrets", "App secrets", "fail", ", ".join(missing), "Use 'Generate secrets' below.")
    return Check("secrets", "App secrets", "ok", "API key and two-factor secret set")


def check_mt5_credentials() -> Check:
    from app.config import settings

    if not settings.mt5_login or not settings.mt5_password or not settings.mt5_server:
        return Check(
            "mt5_credentials", "MT5 account details", "todo",
            "not filled in yet",
            "Enter your login number, password and server name below.",
        )
    return Check(
        "mt5_credentials", "MT5 account details", "ok",
        f"account {settings.mt5_login} on {settings.mt5_server}",
        data={"login": settings.mt5_login, "server": settings.mt5_server},
    )


def check_database() -> Check:
    from sqlalchemy import inspect

    from app.config import settings
    from app.db.base import engine
    from app.db.models import Base

    try:
        inspector = inspect(engine)
        present = set(inspector.get_table_names())
    except Exception as exc:  # noqa: BLE001 - any connection fault is a setup fault here
        return Check(
            "database", "Database", "fail", f"cannot open: {type(exc).__name__}",
            f"Check DATABASE_URL in .env (currently {settings.database_url!r}).",
        )

    expected = set(Base.metadata.tables)
    missing = sorted(expected - present)
    if missing:
        return Check(
            "database", "Database", "todo",
            f"{len(missing)} table(s) missing",
            "Use 'Create tables' below.",
            data={"missing": missing},
        )
    return Check("database", "Database", "ok", f"all {len(expected)} tables present")


def check_terminal() -> Check:
    """Whether the MT5 terminal answers right now, and whether it will trade."""
    if platform.system() != "Windows":
        return Check(
            "terminal", "MT5 terminal", "warn",
            "unavailable on this operating system",
            "The terminal check needs Windows.",
        )
    if importlib.util.find_spec("MetaTrader5") is None:
        return Check("terminal", "MT5 terminal", "fail", "MetaTrader5 package not installed", "Re-run setup.bat.")

    try:
        import MetaTrader5 as mt5

        from app.execution.mt5_adapter import MT5Adapter

        adapter = MT5Adapter(mt5_module=mt5)
        if not adapter.ensure_connected():
            return Check(
                "terminal", "MT5 terminal", "fail",
                f"not reachable: {adapter.last_connect_error}",
                "Is the MT5 terminal open and logged in? Are the account details above correct?",
            )
        terminal = mt5.terminal_info()
        account = mt5.account_info()
        if terminal is not None and getattr(terminal, "trade_allowed", True) is False:
            return Check(
                "terminal", "MT5 terminal", "fail",
                "connected, but algorithmic trading is switched off",
                "In MT5: Tools > Options > Expert Advisors > tick 'Allow algorithmic trading'.",
            )
        if account is None:
            return Check(
                "terminal", "MT5 terminal", "warn", "connected, but the account did not answer",
                "Check the terminal is logged in.",
            )
        is_demo = getattr(account, "trade_mode", None) == getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)
        return Check(
            "terminal", "MT5 terminal", "ok" if is_demo else "warn",
            f"connected to {'demo' if is_demo else 'LIVE'} account {getattr(account, 'login', '?')} "
            f"({getattr(account, 'equity', 0):,.2f} {getattr(account, 'currency', '')})",
            "" if is_demo else "This is a funded account. Start on a demo account instead.",
            data={
                "login": getattr(account, "login", None),
                "server": getattr(account, "server", None),
                "equity": getattr(account, "equity", None),
                "currency": getattr(account, "currency", None),
                "is_demo": is_demo,
            },
        )
    except Exception as exc:  # noqa: BLE001 - report, never crash the wizard
        return Check("terminal", "MT5 terminal", "fail", f"{type(exc).__name__}: {exc}", "")


def check_watchlist() -> Check:
    from app.db.base import SessionLocal
    from app.watchlist.service import list_watchlist

    try:
        session = SessionLocal()
        try:
            rows = list_watchlist(session)
        finally:
            session.close()
    except Exception:  # noqa: BLE001 - almost always "tables not created yet"
        return Check("watchlist", "Instruments", "todo", "cannot read yet", "Create the tables first.")

    if not rows:
        return Check(
            "watchlist", "Instruments", "todo", "nothing chosen yet",
            "Pick what the system is allowed to trade below. Nothing trades until you do.",
        )
    enabled = [r for r in rows if r.enabled]
    return Check(
        "watchlist", "Instruments", "ok",
        f"{len(enabled)} enabled of {len(rows)}: {', '.join(r.instrument for r in rows)}",
        data={
            "instruments": [
                {
                    "instrument": r.instrument,
                    "asset_class": r.asset_class.value,
                    "timeframe": r.timeframe,
                    "enabled": r.enabled,
                }
                for r in rows
            ]
        },
    )


def check_kill_switch() -> Check:
    from app.db.base import SessionLocal
    from app.killswitch.service import get_state

    try:
        session = SessionLocal()
        try:
            state = get_state(session)
        finally:
            session.close()
    except Exception:  # noqa: BLE001
        return Check("kill_switch", "Kill switch", "todo", "cannot read yet", "Create the tables first.")

    if state.engaged:
        return Check(
            "kill_switch", "Kill switch", "warn",
            f"engaged — no new orders ({state.reason})",
            "Re-enable it from the phone app once you've checked why it engaged.",
        )
    return Check("kill_switch", "Kill switch", "ok", "clear — new orders allowed")


ORDERED_CHECKS = (
    check_python,
    check_mt5_package,
    check_env_file,
    check_secrets,
    check_mt5_credentials,
    check_database,
    check_terminal,
    check_watchlist,
    check_kill_switch,
)


def collect() -> list[dict]:
    """Run every check. Each is individually guarded, so one exploding check
    can't blank out the whole page."""
    out = []
    for func in ORDERED_CHECKS:
        try:
            out.append(func().as_dict())
        except Exception as exc:  # noqa: BLE001
            out.append(
                Check(func.__name__, func.__name__, "fail", f"check itself failed: {exc}").as_dict()
            )
    return out


def is_ready_to_trade(checks: list[dict]) -> bool:
    """Whether the blocking prerequisites are met. Warnings don't block — a live
    account or a missing 2FA secret is the operator's call, not ours."""
    blocking = {"python", "mt5_package", "env_file", "secrets", "mt5_credentials", "database", "terminal", "watchlist"}
    by_key = {c["key"]: c["state"] for c in checks}
    return all(by_key.get(key) in ("ok", "warn") for key in blocking)
