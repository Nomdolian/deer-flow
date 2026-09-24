"""Replay a token's price history through one strategy and print the gate.

  python -m scripts.run_replay <token_id> [--strategy s1_maker] [--days 7]

Read backtest/replay.py's caveat first: prices-history has no book depth, so
maker results here are optimistic. This catches broken logic; it does not
prove edge.
"""

from __future__ import annotations

import argparse
import asyncio
import time

import httpx

from pmbot.backtest.metrics import compute, gate_for_live
from pmbot.backtest.replay import ReplayEngine
from pmbot.config import load_config
from pmbot.data.clob_rest import ClobRestClient
from pmbot.data.gamma import GammaClient
from pmbot.fees import FeeTable
from pmbot.strategies.s1_maker import MakerStrategy
from pmbot.strategies.s3_fairvalue import FairValueStrategy
from pmbot.strategies.s4_longshot import LongshotStrategy


def build_strategy(name: str, cfg):
    if name == "s1_maker":
        return MakerStrategy(cfg.strategies.s1_maker, lambda token_id: [],
                             stale_book_max_ms=10 ** 12)
    if name == "s3_fairvalue":
        return FairValueStrategy(cfg.strategies.s3_fairvalue, stale_book_max_ms=10 ** 12)
    if name == "s4_longshot":
        return LongshotStrategy(cfg.strategies.s4_longshot, stale_book_max_ms=10 ** 12)
    raise SystemExit(f"strategy {name} is not replayable from mid-price history")


async def main(token_id: str, strategy_name: str, days: int) -> int:
    cfg = load_config()
    async with httpx.AsyncClient(timeout=30.0) as http:
        rest = ClobRestClient(cfg.host, http)
        gamma = GammaClient(cfg.gamma_host, http)
        series = await rest.get_prices_history(
            token_id, start_ts=int(time.time()) - days * 86400, fidelity=1
        )
        if not series:
            print("no history for that token")
            return 1
        markets = [m for m in await gamma.fetch_markets(limit=1000) if m.token_id == token_id]
        if not markets:
            print("token not found in the active universe")
            return 1

    strategy = build_strategy(strategy_name, cfg)
    strategy.enabled = True
    result = ReplayEngine(strategy, markets[0], FeeTable()).run(series)
    metrics = compute(result.pnls, result.equity_curve)
    ok, failures = gate_for_live(metrics, cfg.risk.daily_loss_kill_pct)

    print(f"{strategy_name} over {len(series)} points of {token_id[:16]}…")
    print(f"  signals={result.signals} fills={len(result.fills)} pnl={metrics.pnl:.2f}")
    print(f"  expectancy={metrics.expectancy:.4f} win_rate={metrics.win_rate:.2%} "
          f"max_dd={metrics.max_drawdown_pct:.2f}% sharpe={metrics.sharpe}")
    print("  live gate: " + ("PASS" if ok else "FAIL"))
    for failure in failures:
        print(f"    - {failure}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("token_id")
    parser.add_argument("--strategy", default="s1_maker")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.token_id, args.strategy, args.days)))
