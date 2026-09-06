"""Phase 0: prove the plumbing before writing a single strategy.

  1. geoblock check          — can this machine place orders at all?
  2. public read             — is the CLOB reachable without auth?
  3. L2 credential derive    — optional, needs PRIVATE_KEY in .env

Run: python -m scripts.phase0_check [--auth]
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx

from pmbot.auth import GeoblockError, authenticate, check_geoblock
from pmbot.config import Secrets, load_config
from pmbot.data.clob_rest import ClobRestClient
from pmbot.data.gamma import GammaClient


async def main(with_auth: bool) -> int:
    cfg = load_config()
    secrets = Secrets()
    async with httpx.AsyncClient(timeout=20.0) as http:
        try:
            geo = await check_geoblock(cfg.geoblock_url, http)
            print(f"[ok]   geoblock clear — country={geo.get('country')} region={geo.get('region')}")
        except GeoblockError as exc:
            print(f"[FAIL] {exc}")
            return 1

        gamma = GammaClient(cfg.gamma_host, http)
        markets = await gamma.fetch_markets(limit=20)
        print(f"[ok]   gamma reachable — {len(markets)} outcome tokens on the first page")
        if not markets:
            return 1

        rest = ClobRestClient(cfg.host, http)
        token_id = markets[0].token_id
        book = await rest.get_book(token_id)
        print(f"[ok]   book for {token_id[:16]}… bid={book.best_bid} ask={book.best_ask}")

        if with_auth:
            if not secrets.private_key:
                print("[FAIL] --auth given but PRIVATE_KEY is not set in .env")
                return 1
            client, creds = await authenticate(cfg, secrets)
            print(f"[ok]   L2 credentials derived for {client.get_address()}")
            print(f"       api_key={creds.api_key[:8]}… (cached to {secrets.creds_cache_path})")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--auth", action="store_true",
                        help="also derive L2 credentials (requires PRIVATE_KEY)")
    sys.exit(asyncio.run(main(parser.parse_args().auth)))
