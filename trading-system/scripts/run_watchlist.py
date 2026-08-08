"""Run WatchlistRunner continuously: the full pipeline across every enabled
watchlist instrument, on one shared paper account, so the risk manager's
portfolio/correlation caps apply across the whole selection — this is the
"pick which assets the bot trades" runner behind the mobile app's Assets
screen.

    python -m scripts.run_watchlist --starting-balance 10000 --poll-seconds 60

Manage the watchlist itself via the API (POST/DELETE /watchlist) or the
mobile app's Assets screen — this script only runs whatever is currently
enabled, re-reading it every cycle so changes take effect without a restart.
"""

import argparse
import time

from app.db.base import SessionLocal, init_db
from app.execution.paper_adapter import PaperAdapter
from app.watchlist.runner import WatchlistRunner


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--starting-balance", type=float, default=10_000.0)
    parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args()

    init_db()
    execution = PaperAdapter(starting_balance=args.starting_balance)
    runner = WatchlistRunner(session_factory=SessionLocal, execution=execution)

    print(f"starting watchlist runner (poll every {args.poll_seconds}s) — manage instruments via /watchlist")
    try:
        while True:
            results = runner.run_once()
            portfolio = results.pop("_portfolio")
            print(f"equity={portfolio['equity']:.2f} open_positions={portfolio['open_positions']} tracked={list(results)}")
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
