"""Read-only journal report from the SQLite file: what traded, what didn't,
and whether the model's predicted edge showed up.

  python -m scripts.report [--db pmbot.sqlite] [--days 7]
"""

from __future__ import annotations

import argparse
import asyncio
import time

from pmbot.backtest.metrics import compute
from pmbot.store.db import Database


async def main(path: str, days: int) -> int:
    since = int(time.time() - days * 86400)
    db = await Database(path).connect()
    try:
        curve = await db.fetch_equity_curve(since)
        fills = await db.fetch_fills(since)
        vetoes = await db.fetch_veto_counts(since)
        stats = await db.fetch_strategy_stats()
    finally:
        await db.close()

    print(f"=== last {days}d ===")
    if curve:
        metrics = compute([], [value for _, value in curve])
        print(f"equity {curve[0][1]:.2f} -> {curve[-1][1]:.2f} "
              f"(max drawdown {metrics.max_drawdown_pct:.2f}%, sharpe {metrics.sharpe})")
    fees = sum(row["fee"] for row in fills)
    makers = len([row for row in fills if row["is_maker"]])
    print(f"fills: {len(fills)} ({makers} maker / {len(fills) - makers} taker), fees ${fees:.2f}")

    print("\nvetoes (why signals did not trade):")
    for reason, count in sorted(vetoes.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {count:6d}  {reason}")

    print("\nper strategy:")
    for row in stats:
        win_rate = row["wins"] / row["trades"] if row["trades"] else 0.0
        print(f"  {row['strategy']:<14} trades={row['trades']:<5} win={win_rate:.0%} "
              f"pnl=${row['pnl']:.2f} weight={row['weight']:.2f}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="pmbot.sqlite")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.db, args.days)))
