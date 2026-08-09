"""Pre-flight check for the MT5 connection. Run this BEFORE the live runner.

    python -m scripts.check_mt5
    python -m scripts.check_mt5 --symbols EURUSD BTCUSD XAUUSD

Verifies the terminal is reachable and algo trading is enabled, reports the
account it's attached to, lists your broker's actual crypto/forex symbol names
(these are broker-specific — `BTCUSD` on one broker is `BTCUSD.a` or `Bitcoin`
on another, and using the wrong name is the most common reason nothing trades),
and shows which of your watchlist symbols are tradeable right now.
"""

import argparse

from app.db.base import SessionLocal
from app.execution.mt5_adapter import SYSTEM_MAGIC, MT5Adapter
from app.watchlist.service import list_watchlist


def _import_mt5():
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise SystemExit(
            "The MetaTrader5 package is not installed or not supported here.\n"
            "It is Windows-only. On Windows: pip install -e \".[mt5]\""
        ) from exc
    return mt5


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*", help="symbols to check; defaults to your watchlist")
    parser.add_argument("--list-crypto", action="store_true", help="list every crypto-looking symbol your broker offers")
    args = parser.parse_args()

    mt5 = _import_mt5()

    print("== connecting ==")
    adapter = MT5Adapter(mt5_module=mt5)
    if not adapter.ensure_connected():
        raise SystemExit(
            f"FAILED: {adapter.last_connect_error}\n\n"
            "Checklist:\n"
            "  - is the MT5 terminal running and logged in?\n"
            "  - Tools > Options > Expert Advisors > 'Allow algorithmic trading' enabled?\n"
            "  - are MT5_LOGIN / MT5_PASSWORD / MT5_SERVER correct in .env?\n"
            "  - if MT5 is installed somewhere unusual, set MT5_PATH\n"
        )
    print("connected OK")

    terminal = mt5.terminal_info()
    account = mt5.account_info()
    if terminal is not None:
        algo = getattr(terminal, "trade_allowed", None)
        print(f"terminal: build={getattr(terminal, 'build', '?')} trade_allowed={algo}")
        if algo is False:
            print("  WARNING: algorithmic trading is DISABLED — the system cannot place orders.")
            print("  Fix: Tools > Options > Expert Advisors > Allow algorithmic trading")
    if account is not None:
        print(
            f"account: #{getattr(account, 'login', '?')} "
            f"({getattr(account, 'server', '?')}) "
            f"equity={getattr(account, 'equity', 0):.2f} "
            f"currency={getattr(account, 'currency', '?')} "
            f"trade_mode={'DEMO' if getattr(account, 'trade_mode', 0) == 0 else 'LIVE/CONTEST'}"
        )
    print(f"system magic number: {SYSTEM_MAGIC} (only positions with this magic are managed)")

    if args.list_crypto:
        print("\n== crypto-looking symbols your broker offers ==")
        all_symbols = mt5.symbols_get() or []
        needles = ("BTC", "ETH", "XRP", "DOGE", "SOL", "LTC", "BCH", "ADA", "Bitcoin", "Ether")
        found = [s.name for s in all_symbols if any(n.upper() in s.name.upper() for n in needles)]
        if found:
            for name in sorted(found):
                print(f"  {name}")
            print("\nUse these EXACT names when adding crypto to your watchlist.")
        else:
            print("  none found — your broker may not offer crypto CFDs on this account")

    symbols = args.symbols
    if not symbols:
        session = SessionLocal()
        try:
            symbols = [w.instrument for w in list_watchlist(session)]
        finally:
            session.close()

    if not symbols:
        print("\nNo symbols to check (watchlist is empty). Pass --symbols, or add some via /watchlist.")
        return

    print("\n== symbol check ==")
    any_bad = False
    for symbol in symbols:
        info = mt5.symbol_info(symbol)
        if info is None:
            print(f"  {symbol:<14} NOT FOUND at this broker — check the exact name (try --list-crypto)")
            any_bad = True
            continue
        tick = mt5.symbol_info_tick(symbol)
        tradeable = adapter.is_tradeable(symbol)
        price = f"{tick.bid:.5f}/{tick.ask:.5f}" if tick else "no tick"
        state = "TRADEABLE" if tradeable else "closed/disabled right now"
        print(f"  {symbol:<14} {state:<26} {price}  min_lot={getattr(info, 'volume_min', '?')}")
        if not tradeable:
            print("      (not an error if the market is simply closed — weekends for forex, "
                  "maintenance windows for crypto)")

    if any_bad:
        print("\nFix the NOT FOUND symbols before running the live runner — the system fails closed "
              "on unknown symbols and will simply never trade them.")


if __name__ == "__main__":
    main()
