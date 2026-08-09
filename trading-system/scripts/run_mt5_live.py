"""The 24/7 runner: trades your MT5 watchlist continuously against the MT5
terminal on this machine.

    python -m scripts.run_mt5_live --poll-seconds 30

What it does on every start (in this order, deliberately):
  1. Reconciles the DB against the broker — anything that closed while this
     process was down (SL/TP hit) gets journaled with its real exit price, so
     the risk manager isn't holding phantom risk and strategy stats stay true.
  2. Starts the connectivity watchdog — a prolonged broker outage or a run of
     execution errors engages the kill switch (Phase 0, item 3).
  3. Loops the watchlist: data -> signals -> risk -> execution -> journal.

Requires (Windows):
  - MT5 terminal installed, logged in, and RUNNING (this attaches to it)
  - Tools > Options > Expert Advisors > "Allow algorithmic trading" enabled
  - `pip install -e ".[mt5]"`
  - MT5_LOGIN / MT5_PASSWORD / MT5_SERVER in .env (MT5_PATH if not the default
    install location)

Crypto note: 24/7 depends on your broker listing crypto CFD symbols (BTCUSD
etc.). The runner asks MT5 whether each symbol is tradeable right now rather
than assuming, so a broker's daily maintenance window is treated as expected
quiet, not a fault.

Safety properties worth knowing:
  - Every order carries its stop-loss and take-profit broker-side, so open
    positions stay protected even if this process dies, the laptop sleeps, or
    the kill switch engages. Halting only ever stops NEW orders.
  - Only positions stamped with this system's magic number are visible to it;
    trades you place by hand in the terminal are never sized against or closed.
"""

import argparse
import time

from app.db.base import SessionLocal, init_db
from app.execution.mt5_adapter import MT5Adapter
from app.health.watchdog import (
    DEFAULT_MAX_CONSECUTIVE_EXECUTION_ERRORS,
    DEFAULT_MAX_DISCONNECTED_SECONDS,
    ConnectivityWatchdog,
)
from app.journal.reconcile import reconcile_positions
from app.killswitch.service import get_state
from app.logging_utils import log_decision
from app.watchlist.runner import WatchlistRunner
from app.watchlist.service import list_watchlist


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--max-disconnected-seconds", type=int, default=DEFAULT_MAX_DISCONNECTED_SECONDS)
    parser.add_argument(
        "--max-consecutive-execution-errors", type=int, default=DEFAULT_MAX_CONSECUTIVE_EXECUTION_ERRORS
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=0,
        help="Stop after this many cycles instead of running forever. Use it to smoke-test a fresh "
        "install (--max-cycles 3) without leaving a process running. 0 means run until stopped.",
    )
    parser.add_argument(
        "--skip-reconcile",
        action="store_true",
        help="Skip startup reconciliation. Only for debugging — without it, trades that closed while "
        "this process was down stay open in the journal and keep consuming the portfolio risk cap.",
    )
    args = parser.parse_args()

    init_db()

    print("connecting to MT5 terminal...")
    execution = MT5Adapter()
    if not execution.ensure_connected():
        raise SystemExit(
            f"could not reach the MT5 terminal: {execution.last_connect_error}\n"
            "Check that the terminal is running, logged in, and that algorithmic trading is enabled."
        )
    print(f"connected — account equity {execution.get_equity():.2f}")

    session = SessionLocal()
    try:
        if not args.skip_reconcile:
            report = reconcile_positions(session, execution)
            print(f"reconciled: {report.summary()}")
            if report.untracked_at_broker:
                print(
                    "  note: untracked positions at the broker (reported only, never touched): "
                    + ", ".join(report.untracked_at_broker)
                )
            if report.unresolved:
                print(f"  warning: {len(report.unresolved)} trade(s) could not be resolved — review the journal")

        watchlist = list_watchlist(session, enabled_only=True)
        if not watchlist:
            print("watchlist is empty — enable instruments via the mobile Assets tab or POST /watchlist")
        else:
            print(f"trading {len(watchlist)} instrument(s): {', '.join(w.instrument for w in watchlist)}")

        kill_switch = get_state(session)
        if kill_switch.engaged:
            print(f"NOTE: kill switch is ENGAGED ({kill_switch.reason}) — no new orders until you re-enable it")

        log_decision(
            session,
            event_type="mt5_live_runner_started",
            agent_id="run_mt5_live",
            payload={"poll_seconds": args.poll_seconds, "instruments": [w.instrument for w in watchlist]},
        )
    finally:
        session.close()

    watchdog = ConnectivityWatchdog(
        SessionLocal,
        execution,
        max_disconnected_seconds=args.max_disconnected_seconds,
        max_consecutive_execution_errors=args.max_consecutive_execution_errors,
    )
    runner = WatchlistRunner(session_factory=SessionLocal, execution=execution, watchdog=watchdog)

    limit = f", stopping after {args.max_cycles} cycle(s)" if args.max_cycles else " — Ctrl+C to stop"
    print(f"loop started, polling every {args.poll_seconds}s{limit}")
    cycle = 0
    try:
        while True:
            try:
                results = runner.run_once()
                portfolio = results.pop("_portfolio")
                if not portfolio.get("connected", True):
                    outage = watchdog.disconnected_since
                    print(f"broker disconnected (since {outage}) — not trading this cycle")
                else:
                    print(
                        f"equity={portfolio['equity']:.2f} "
                        f"open_positions={portfolio['open_positions']} tracked={list(results)}"
                    )
            except Exception as exc:  # noqa: BLE001 - one bad cycle must not kill a 24/7 process
                # Log and keep looping. The alternative (crashing) means the
                # process is dead until you notice, with positions unmanaged.
                session = SessionLocal()
                try:
                    log_decision(
                        session,
                        event_type="runner_cycle_error",
                        agent_id="run_mt5_live",
                        payload={"error": str(exc)},
                    )
                finally:
                    session.close()
                print(f"cycle error (continuing): {exc}")

            cycle += 1
            if args.max_cycles and cycle >= args.max_cycles:
                print(f"completed {cycle} cycle(s), stopping as asked")
                return
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
