"""Prove the order path end to end by placing and immediately closing ONE
minimum-size trade on a DEMO account.

    python -m scripts.verify_mt5_trade --symbol EURUSD

Everything else in the pre-flight sequence checks that the system *could*
trade. This checks that it *does*: symbol resolution, contract terms, lot
conversion, filling mode, broker-side stop and target, the magic number, finding
the position again by ticket, and closing it. Those are exactly the steps that
fail silently on a live account and look like "the bot just never trades".

Run this once when you set up a broker, and again any time you change broker,
account type, or symbol.

Safety rails, in order:
  1. Refuses to run unless MT5 reports a DEMO account. Override only with
     --allow-live, which also requires typing the confirmation phrase.
  2. Uses the broker's MINIMUM lot size, not a risk-derived size.
  3. Closes the position immediately, and reports loudly if the close fails.
  4. Writes nothing to the trade journal — this is a broker test, not a trade
     the learning loop should study.
"""

import argparse
import time
import uuid

from app.db.models import Direction
from app.execution.base import OrderRequest
from app.execution.mt5_adapter import SYSTEM_MAGIC, MT5Adapter

CONFIRM_PHRASE = "I accept a real trade on a funded account"


def _import_mt5():
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise SystemExit(
            "The MetaTrader5 package is not installed or not supported here.\n"
            'It is Windows-only. On Windows: pip install -e ".[mt5]"'
        ) from exc
    return mt5


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="EURUSD", help="symbol to test (use your broker's exact name)")
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="permit running against a funded account (requires typing a confirmation phrase)",
    )
    parser.add_argument("--hold-seconds", type=float, default=2.0, help="how long to hold before closing")
    args = parser.parse_args()

    mt5 = _import_mt5()

    print("== connecting ==")
    adapter = MT5Adapter(mt5_module=mt5)
    if not adapter.ensure_connected():
        raise SystemExit(f"FAILED to connect: {adapter.last_connect_error}\nRun scripts.check_mt5 first.")
    print("connected")

    terminal = mt5.terminal_info()
    if terminal is not None and getattr(terminal, "trade_allowed", True) is False:
        raise SystemExit(
            "Algorithmic trading is DISABLED in the terminal, so no order can be placed.\n"
            "Fix: Tools > Options > Expert Advisors > Allow algorithmic trading"
        )

    account = mt5.account_info()
    if account is None:
        raise SystemExit("Could not read account info — cannot confirm this is a demo account. Refusing to trade.")

    is_demo = getattr(account, "trade_mode", None) == getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)
    print(
        f"account #{getattr(account, 'login', '?')} on {getattr(account, 'server', '?')} — "
        f"{'DEMO' if is_demo else 'LIVE or CONTEST'}, equity {getattr(account, 'equity', 0):.2f} "
        f"{getattr(account, 'currency', '')}"
    )

    if not is_demo:
        if not args.allow_live:
            raise SystemExit(
                "\nThis is not a demo account, so this script will not place an order.\n"
                "Point MT5 at a demo account and re-run. If you genuinely intend to place a real\n"
                "minimum-size trade on funded money, re-run with --allow-live."
            )
        print(f"\nThis will place a REAL order on funded money. Type exactly:\n  {CONFIRM_PHRASE}")
        if input("> ").strip() != CONFIRM_PHRASE:
            raise SystemExit("Confirmation did not match. Nothing was placed.")

    symbol = args.symbol
    print(f"\n== {symbol} ==")
    if not adapter.is_tradeable(symbol):
        raise SystemExit(
            f"{symbol} is not tradeable right now. If the market is simply closed (forex on a weekend, "
            "a crypto maintenance window) wait and re-run — this is not a fault."
        )

    spec = adapter.get_symbol_spec(symbol)
    if spec is None:
        raise SystemExit(f"Could not read contract terms for {symbol}. Check the exact symbol name.")
    print(
        f"contract size {spec.contract_size:g} · lots {spec.volume_min:g}-{spec.volume_max:g} "
        f"step {spec.volume_step:g} · {spec.digits} digits · "
        f"broker minimum stop distance {spec.min_stop_distance():.{spec.digits}f}"
    )

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise SystemExit(f"No tick data for {symbol} — the symbol may not be in Market Watch.")

    # Minimum lot, expressed back in instrument units because that's what
    # place_order takes (it converts to lots itself, using the same spec).
    units = spec.volume_min * spec.contract_size
    # Stops set well outside the broker's minimum distance, and outside normal
    # noise, so this test fails on order rejection rather than on being stopped
    # out in the two seconds it's open.
    pad = max(spec.min_stop_distance() * 3, tick.ask * 0.01)
    stop_loss = spec.round_price(tick.ask - pad)
    take_profit = spec.round_price(tick.ask + pad)

    print(
        f"placing BUY {spec.volume_min:g} lots at ~{tick.ask:.{spec.digits}f}, "
        f"SL {stop_loss:.{spec.digits}f} / TP {take_profit:.{spec.digits}f}, magic {SYSTEM_MAGIC}"
    )

    client_order_id = f"verify-{uuid.uuid4().hex[:12]}"
    placed = adapter.place_order(
        OrderRequest(
            client_order_id=client_order_id,
            instrument=symbol,
            direction=Direction.long,
            size=units,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
    )

    if placed.status != "filled":
        raise SystemExit(
            f"\nORDER REJECTED: {placed.error}\n\n"
            "This is the useful outcome — it failed here instead of silently failing at 3am.\n"
            "Common causes:\n"
            "  invalid volume        the broker's lot step/min differs from what was sent\n"
            "  invalid stops         SL/TP inside the broker's minimum distance\n"
            "  unsupported filling   this symbol wants a different filling mode\n"
            "  trade disabled        algo trading off, or the symbol is close-only\n"
            "  no money              insufficient margin for even the minimum lot\n"
        )

    ticket = placed.broker_position_id
    print(f"FILLED at {placed.filled_price} — broker position ticket {ticket}")

    positions = [p for p in adapter.get_open_positions() if p.broker_position_id == ticket]
    if not positions:
        print("WARNING: the position filled but could not be found again by ticket. Close it manually.")
    else:
        pos = positions[0]
        print(
            f"found it again: {pos.instrument} {pos.direction.value} {pos.size:g} lots "
            f"entry {pos.entry_price} SL {pos.stop_loss} TP {pos.take_profit}"
        )
        if not pos.stop_loss:
            print("WARNING: no stop-loss attached at the broker. That is the property that protects you "
                  "if this machine dies mid-trade — investigate before trading for real.")

    print(f"holding {args.hold_seconds:g}s, then closing...")
    time.sleep(args.hold_seconds)

    closed = adapter.close_position(ticket)
    if closed.status != "filled":
        raise SystemExit(
            f"\nCLOSE FAILED: {closed.error}\n\n"
            f"There is an OPEN POSITION (ticket {ticket}) that this script could not close.\n"
            "Close it in the MT5 terminal now, then investigate — a system that can open but not "
            "close positions must not be left running."
        )
    print(f"closed at {closed.filled_price}")

    remaining = [p for p in adapter.get_open_positions() if p.broker_position_id == ticket]
    if remaining:
        raise SystemExit(f"Broker still reports position {ticket} as open. Check the terminal.")

    print(
        "\nThe order path works end to end: sizing, filling mode, broker-side stops, "
        "position lookup and close.\n"
        "Nothing was written to the trade journal — this was a broker test, not a strategy trade.\n"
        "Next: python -m scripts.run_mt5_live --poll-seconds 30"
    )


if __name__ == "__main__":
    main()
