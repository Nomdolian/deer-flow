"""Setup doctor: checks everything the system needs and tells you exactly
what's wrong and how to fix it.

    python -m scripts.doctor

Run this after each setup step. It's ordered by dependency, so the first
FAIL is the one to fix — later checks may fail simply because an earlier
one did. Exits non-zero if anything is broken, so it also works as a
pre-flight gate in a script.
"""

import argparse
import importlib.util
import platform
import sys

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
RESET = "\033[0m"

# Windows consoles don't always handle ANSI or non-ASCII glyphs.
if platform.system() == "Windows":
    GREEN = RED = YELLOW = DIM = RESET = ""
    OK, FAIL, WARN = "[ OK ]", "[FAIL]", "[WARN]"
else:
    OK, FAIL, WARN = f"{GREEN}✓{RESET}", f"{RED}✗{RESET}", f"{YELLOW}!{RESET}"


class Doctor:
    def __init__(self):
        self.failures = 0
        self.warnings = 0

    def ok(self, label: str, detail: str = "") -> None:
        print(f"  {OK} {label}" + (f" {DIM}{detail}{RESET}" if detail else ""))

    def fail(self, label: str, fix: str) -> None:
        self.failures += 1
        print(f"  {FAIL} {label}")
        for line in fix.strip().splitlines():
            print(f"        {line.strip()}")

    def warn(self, label: str, note: str) -> None:
        self.warnings += 1
        print(f"  {WARN} {label}")
        for line in note.strip().splitlines():
            print(f"        {line.strip()}")

    def section(self, title: str) -> None:
        print(f"\n{title}")


def check_python(doc: Doctor) -> None:
    doc.section("Python")
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 12):
        doc.ok(f"Python {major}.{minor}")
    else:
        doc.fail(
            f"Python {major}.{minor} is too old (need 3.12+)",
            "Install Python 3.12 from python.org, then recreate the venv:\n"
            "uv venv --python 3.12 .venv",
        )


def check_dependencies(doc: Doctor) -> None:
    doc.section("Dependencies")
    required = ["fastapi", "sqlalchemy", "psycopg", "pandas", "numpy", "pydantic", "apscheduler", "httpx", "pyotp"]
    missing = [m for m in required if importlib.util.find_spec(m) is None]
    if missing:
        doc.fail(
            f"missing packages: {', '.join(missing)}",
            'uv pip install -e ".[dev]"',
        )
    else:
        doc.ok(f"{len(required)} core packages present")

    if importlib.util.find_spec("anthropic") is None:
        doc.warn(
            "anthropic not installed — the LLM analyst layer will be skipped",
            'Optional. Install with: uv pip install -e ".[dev]"',
        )


def check_env(doc: Doctor) -> None:
    doc.section("Configuration (.env)")
    from pathlib import Path

    if not Path(".env").exists():
        doc.fail(
            ".env not found",
            "Copy the template and fill it in:\n"
            "  Windows:  Copy-Item .env.example .env\n"
            "  Mac/Linux: cp .env.example .env",
        )
        return

    from app.config import settings

    doc.ok(".env found")

    if settings.api_auth_secret in ("", "change-me-to-a-long-random-value"):
        doc.fail(
            "API_AUTH_SECRET is unset or still the placeholder",
            "This is the password your phone uses to reach the API. Generate one:\n"
            '  python -c "import secrets; print(secrets.token_hex(32))"\n'
            "then put it in .env as API_AUTH_SECRET=...",
        )
    elif len(settings.api_auth_secret) < 32:
        doc.warn(
            "API_AUTH_SECRET is short",
            "Anyone with it can read your positions and hit the kill switch. Use 32+ chars.",
        )
    else:
        doc.ok("API_AUTH_SECRET set")

    if not settings.api_totp_secret:
        doc.warn(
            "API_TOTP_SECRET not set — disengaging the kill switch won't require 2FA",
            "Recommended before live money. Generate one:\n"
            '  python -c "import secrets,base64; print(base64.b32encode(secrets.token_bytes(10)).decode())"',
        )
    else:
        doc.ok("API_TOTP_SECRET set (kill-switch disengage is 2FA-gated)")


def check_database(doc: Doctor) -> None:
    doc.section("Database")
    try:
        from sqlalchemy import inspect, text

        from app.db.base import engine
    except Exception as exc:  # noqa: BLE001
        doc.fail(f"could not load the DB layer: {exc}", 'uv pip install -e ".[dev]"')
        return

    from app.config import settings

    is_sqlite = settings.database_url.startswith("sqlite")

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        if is_sqlite:
            doc.fail(
                f"cannot open the SQLite file: {type(exc).__name__}",
                "Usually a bad path. An ABSOLUTE path needs FOUR slashes:\n"
                "  Windows:   DATABASE_URL=sqlite:///C:/trading-system/trading.db\n"
                "  Mac/Linux: DATABASE_URL=sqlite:////home/you/trading-system/trading.db\n"
                "A relative path (three slashes) is fine too: sqlite:///./trading.db\n"
                "Also check the folder exists and is writable.\n"
                f"Currently trying: {_redact_dsn()}",
            )
        else:
            doc.fail(
                f"cannot connect to Postgres: {type(exc).__name__}",
                "Start it, then re-run:\n"
                "  docker compose up -d postgres\n"
                "Or switch to SQLite (no install, no server) in .env:\n"
                "  DATABASE_URL=sqlite:///./trading.db\n"
                f"Currently trying: {_redact_dsn()}",
            )
        return

    doc.ok("SQLite file opened" if is_sqlite else "Postgres reachable")

    expected = {
        "orders",
        "signals",
        "trade_journal",
        "risk_decisions",
        "strategy_versions",
        "kill_switch_state",
        "feed_health",
        "decision_log",
        "devices",
        "watchlist_instruments",
    }
    present = set(inspect(engine).get_table_names())
    missing = expected - present
    if missing:
        doc.fail(
            f"{len(missing)} table(s) missing: {', '.join(sorted(missing))}",
            "python -m scripts.init_db",
        )
    else:
        doc.ok(f"all {len(expected)} tables present")


def _redact_dsn() -> str:
    from app.config import settings

    dsn = settings.database_url
    if "@" in dsn and "//" in dsn:
        scheme, rest = dsn.split("//", 1)
        creds, host = rest.split("@", 1)
        user = creds.split(":", 1)[0]
        return f"{scheme}//{user}:***@{host}"
    return dsn


def check_watchlist(doc: Doctor) -> None:
    doc.section("Watchlist")
    try:
        from app.db.base import SessionLocal
        from app.watchlist.service import list_watchlist
    except Exception as exc:  # noqa: BLE001
        doc.fail(f"could not load the watchlist service: {exc}", "Fix the errors above first.")
        return

    session = SessionLocal()
    try:
        rows = list_watchlist(session)
    except Exception as exc:  # noqa: BLE001
        doc.fail(f"could not read the watchlist: {type(exc).__name__}", "python -m scripts.init_db")
        return
    finally:
        session.close()

    if not rows:
        doc.warn(
            "watchlist is empty — nothing will trade",
            "Add instruments from the mobile Assets tab, or:\n"
            '  curl -X POST -H "x-api-key: $KEY" -H "Content-Type: application/json" \\\n'
            '       -d \'{"instrument":"EURUSD","asset_class":"forex","timeframe":"M15","data_source":"mt5"}\' \\\n'
            "       http://localhost:8000/watchlist",
        )
        return

    enabled = [r for r in rows if r.enabled]
    doc.ok(f"{len(rows)} instrument(s), {len(enabled)} enabled")
    for row in rows:
        state = "enabled" if row.enabled else "disabled"
        print(f"        {row.instrument:<14} {row.timeframe:<5} {row.data_source:<14} {state}")

    mt5_rows = [r for r in enabled if r.data_source == "mt5"]
    if mt5_rows and platform.system() != "Windows":
        doc.warn(
            f"{len(mt5_rows)} instrument(s) use data_source=mt5, but this is not Windows",
            "MT5's Python API is Windows-only. Either run this on Windows, or switch those\n"
            "rows to data_source=alphavantage.",
        )


def check_kill_switch(doc: Doctor) -> None:
    doc.section("Kill switch")
    try:
        from app.db.base import SessionLocal
        from app.killswitch.service import get_state
    except Exception as exc:  # noqa: BLE001
        doc.fail(f"could not load the kill switch: {exc}", "Fix the errors above first.")
        return

    session = SessionLocal()
    try:
        state = get_state(session)
    except Exception as exc:  # noqa: BLE001
        doc.fail(f"could not read kill-switch state: {type(exc).__name__}", "python -m scripts.init_db")
        return
    finally:
        session.close()

    if state.engaged:
        doc.warn(
            f"kill switch is ENGAGED — no new orders will be placed ({state.reason})",
            "This is not necessarily wrong; it may have auto-engaged after an outage or a\n"
            "loss limit. Re-enable from the mobile app once you've checked why.",
        )
    else:
        doc.ok("not engaged (new orders allowed)")


def check_mt5(doc: Doctor, *, skip: bool) -> None:
    doc.section("MT5 terminal")
    if skip:
        print(f"  {DIM}skipped (--skip-mt5){RESET}")
        return

    if platform.system() != "Windows":
        doc.warn(
            f"not Windows ({platform.system()}) — MT5 live trading unavailable here",
            "MT5's Python API only works on Windows. Paper trading and Alpha Vantage data\n"
            "work fine on any OS; live MT5 execution needs a Windows host.",
        )
        return

    if importlib.util.find_spec("MetaTrader5") is None:
        doc.fail(
            "MetaTrader5 package not installed",
            'uv pip install -e ".[mt5]"',
        )
        return

    try:
        from app.config import settings
        from app.execution.mt5_adapter import MT5Adapter

        adapter = MT5Adapter()
    except Exception as exc:  # noqa: BLE001
        doc.fail(
            f"could not initialize MT5: {exc}",
            "Checklist:\n"
            "  - is the MT5 terminal running and logged in?\n"
            "  - Tools > Options > Expert Advisors > 'Allow algorithmic trading' enabled?\n"
            "  - are MT5_LOGIN / MT5_PASSWORD / MT5_SERVER correct in .env?\n"
            "  - non-default install location? set MT5_PATH",
        )
        return

    if not adapter.ensure_connected():
        doc.fail(
            f"MT5 terminal not reachable: {adapter.last_connect_error}",
            "Open the MT5 terminal, log in, and enable algorithmic trading.",
        )
        return

    doc.ok("terminal reachable")

    try:
        import MetaTrader5 as mt5

        terminal = mt5.terminal_info()
        account = mt5.account_info()
    except Exception:  # noqa: BLE001
        terminal = account = None

    if terminal is not None and getattr(terminal, "trade_allowed", True) is False:
        doc.fail(
            "algorithmic trading is DISABLED in the terminal — orders cannot be placed",
            "Tools > Options > Expert Advisors > tick 'Allow algorithmic trading'",
        )
    elif terminal is not None:
        doc.ok("algorithmic trading enabled")

    if account is not None:
        is_demo = getattr(account, "trade_mode", 0) == 0
        label = "DEMO" if is_demo else "LIVE"
        doc.ok(
            f"account #{getattr(account, 'login', '?')} ({label})",
            f"equity={getattr(account, 'equity', 0):.2f} {getattr(account, 'currency', '')}",
        )
        if not is_demo:
            doc.warn(
                "this is a LIVE account",
                "Run on a demo account for weeks first. See the Build Order in README.md —\n"
                "backtest, then walk-forward, then paper, then real money.",
            )

    # Verify the watchlist's symbols actually resolve at this broker.
    try:
        from app.db.base import SessionLocal
        from app.watchlist.service import list_watchlist

        session = SessionLocal()
        try:
            symbols = [r.instrument for r in list_watchlist(session) if r.enabled]
        finally:
            session.close()
    except Exception:  # noqa: BLE001
        symbols = []

    if symbols:
        import MetaTrader5 as mt5

        bad = [s for s in symbols if mt5.symbol_info(s) is None]
        if bad:
            doc.fail(
                f"symbol(s) not found at this broker: {', '.join(bad)}",
                "Broker symbol names vary (BTCUSD vs BTCUSD.a vs Bitcoin). List yours:\n"
                "  python -m scripts.check_mt5 --list-crypto\n"
                "The system fails closed on unknown symbols, so these would silently never trade.",
            )
        else:
            tradeable = [s for s in symbols if adapter.is_tradeable(s)]
            doc.ok(
                f"all {len(symbols)} watchlist symbol(s) resolve",
                f"{len(tradeable)} tradeable right now (the rest are closed markets, which is normal)",
            )

        # Resolving a symbol says nothing about whether an order would be
        # accepted on it. Check the sizing arithmetic that actually decides.
        untradeable_size = []
        for symbol in symbols:
            spec = adapter.get_symbol_spec(symbol)
            if spec is None:
                continue
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                continue
            equity = adapter.get_equity() if adapter.ensure_connected() else 0.0
            risk_amount = equity * settings.risk_per_trade_pct
            if risk_amount <= 0:
                continue
            mid = (tick.bid + tick.ask) / 2
            _, error = adapter.resolve_volume(symbol, risk_amount / (mid * 0.01))
            if error is not None:
                untradeable_size.append(symbol)
        if untradeable_size:
            doc.warn(
                f"{', '.join(untradeable_size)}: position would fall under the broker's minimum lot",
                f"At {settings.risk_per_trade_pct:.2%} risk this account is too small for these symbols, so\n"
                "the system will reject rather than round up past the risk you authorized. Details:\n"
                "  python -m scripts.check_mt5",
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-mt5", action="store_true", help="skip MT5 checks (paper/data-only setups)")
    args = parser.parse_args()

    print("Trading system setup check")
    print("=" * 60)

    doc = Doctor()
    check_python(doc)
    check_dependencies(doc)
    check_env(doc)
    check_database(doc)
    check_watchlist(doc)
    check_kill_switch(doc)
    check_mt5(doc, skip=args.skip_mt5)

    print("\n" + "=" * 60)
    if doc.failures:
        print(f"{doc.failures} problem(s) to fix" + (f", {doc.warnings} warning(s)" if doc.warnings else ""))
        print("Fix the FIRST failure above and re-run — later checks often fail only because an earlier one did.")
        raise SystemExit(1)

    if doc.warnings:
        print(f"No blocking problems. {doc.warnings} warning(s) above worth reading.")
    else:
        print("All checks passed.")
    print(
        "\nNext: python -m scripts.verify_mt5_trade   (proves the order path on a demo account)"
        "\n      python -m scripts.run_mt5_live       (or run_watchlist for paper trading)"
    )


if __name__ == "__main__":
    main()
