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
    print(f"venue  {cfg.venue}  ({cfg.host})")
    async with httpx.AsyncClient(timeout=20.0) as http:
        try:
            geo = await check_geoblock(cfg.geoblock_url, http)
            print(f"[ok]   geoblock clear — country={geo.get('country')} region={geo.get('region')}")
        except GeoblockError as exc:
            print(f"[FAIL] {exc}")
            return 1
        except httpx.HTTPError as exc:
            return _unreachable("geoblock", cfg.geoblock_url, exc)

        gamma = GammaClient(cfg.gamma_host, http)
        try:
            markets = await gamma.fetch_markets(limit=20)
        except httpx.HTTPError as exc:
            return _unreachable("gamma", cfg.gamma_host, exc)
        print(f"[ok]   gamma reachable — {len(markets)} outcome tokens on the first page")
        if not markets:
            print("[FAIL] gamma returned no active markets — is the host right for this venue?")
            return 1

        rest = ClobRestClient(cfg.host, http)
        token_id = markets[0].token_id
        try:
            book = await rest.get_book(token_id)
        except httpx.HTTPError as exc:
            return _unreachable("clob", cfg.host, exc)
        print(f"[ok]   book for {token_id[:16]}… bid={book.best_bid} ask={book.best_ask}")

        if with_auth:
            if not secrets.private_key:
                print("[FAIL] --auth given but PRIVATE_KEY is not set in .env")
                return 1
            try:
                client, creds = await authenticate(cfg, secrets)
            except Exception as exc:  # noqa: BLE001 - SDK raises a family of errors
                print(f"[FAIL] could not derive L2 credentials: {exc}")
                return 1
            print(f"[ok]   L2 credentials derived for {client.get_address()}")
            print(f"       api_key={creds.api_key[:8]}… (cached to {secrets.creds_cache_path})")
    return 0


def _unreachable(label: str, host: str, exc: Exception) -> int:
    """One clean line instead of a stack trace: at this stage the answer is
    almost always a proxy, a firewall, or the wrong venue.
    """
    print(f"[FAIL] {label} unreachable at {host}")
    print(f"       {type(exc).__name__}: {exc}")
    print("       check your network (proxy/VPN/firewall) and that `venue` in "
          "bot.yaml matches the hosts you can reach.")
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--auth", action="store_true",
                        help="also derive L2 credentials (requires PRIVATE_KEY)")
    sys.exit(asyncio.run(main(parser.parse_args().auth)))
