"""Manual settlement sweep: merge complete sets and redeem resolved positions.

The bot does this hourly on its own. This is the same code path for when you
want to run it by hand — after a big resolution day, or before shutting the
bot down for a while.

    python -m scripts.settle                # dry run against your live book
    python -m scripts.settle --send         # actually sends the transactions
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx

from pmbot.chain import ChainClient, load_contracts
from pmbot.config import Secrets, load_config
from pmbot.data.gamma import GammaClient
from pmbot.execution.redeem import ChainRedeemer, RedeemWorker, SetMerger
from pmbot.logging_utils import setup_logging
from pmbot.state import Portfolio, Position
from pmbot.store.db import Database


async def main(send: bool, db_path: str) -> int:
    setup_logging()
    cfg = load_config()
    secrets = Secrets()
    dry_run = not send

    # Positions come from the bot's own book, so what is settled is exactly
    # what the bot thinks it holds.
    db = await Database(db_path).connect()
    try:
        async with db.db.execute(
            "SELECT token_id, qty, avg_px, realized FROM positions WHERE qty > 0"
        ) as cursor:
            rows = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()
    if not rows:
        print("no open positions on the books.")
        return 0

    portfolio = Portfolio()
    for row in rows:
        portfolio.positions[row["token_id"]] = Position(
            token_id=row["token_id"], qty=row["qty"], avg_px=row["avg_px"],
            realized=row["realized"],
        )

    async with httpx.AsyncClient(timeout=30.0) as http:
        gamma = GammaClient(cfg.gamma_host, http)
        discovered = await gamma.fetch_markets(limit=2000)
        markets = {m.token_id: m for m in discovered if m.token_id in portfolio.positions}
        missing = set(portfolio.positions) - set(markets)
        if missing:
            print(f"note: {len(missing)} held tokens are no longer listed as active "
                  f"markets; they are resolved or delisted and need the chain to settle.")

        chain = None
        if secrets.private_key:
            chain = ChainClient(cfg.chain.rpc_url, secrets.private_key,
                                load_contracts(cfg.chain_id, cfg.chain.contracts), cfg.chain_id)

        merger = SetMerger(chain, portfolio, markets, mode="live", dry_run=dry_run)
        sets = merger.complete_sets()
        print(f"complete sets held: {len(sets)}")
        merged = await merger.sweep()

        worker = RedeemWorker(gamma, portfolio, markets, mode="live",
                              redeemer=ChainRedeemer(chain, dry_run) if chain else None,
                              alert=print, min_payout_usd=cfg.chain.min_redeem_usd)
        redeemed = await worker.sweep()

    print(f"merged:   {len(merged)} sets, ${sum(m.proceeds for m in merged):.2f}")
    print(f"redeemed: {len(redeemed)} positions, ${sum(r.payout for r in redeemed):.2f}")
    if dry_run:
        print("\n(dry run — nothing was sent. Re-run with --send to settle for real.)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--send", action="store_true", help="submit transactions (costs gas)")
    parser.add_argument("--db", default="pmbot.sqlite")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.send, args.db)))
