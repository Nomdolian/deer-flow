"""Client construction, L2 credential derivation, and the geoblock gate.

Auth model:
  L1 — an EIP-712 signature from your private key, used once to mint L2 creds.
  L2 — HMAC-SHA256 headers on every trading request.
  Public reads need neither.

Deriving credentials is free and idempotent: the same key always yields the
same creds, so they are cached to disk and re-derived only if the cache is
missing. Nothing here ever writes the private key anywhere.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path

import httpx

from pmbot.config import BotConfig, Secrets

log = logging.getLogger(__name__)

# signature_type 3 (POLY_1271 / V2 deposit wallet) is knowingly excluded:
# create_or_derive_api_key signs the L1 headers with the EOA, so the key
# registers against the wrong address and every order fails signer != api_key.
SUPPORTED_SIGNATURE_TYPES = {0, 1, 2}


@dataclass
class ApiCreds:
    api_key: str
    api_secret: str
    api_passphrase: str

    def to_dict(self) -> dict:
        return {
            "api_key": self.api_key,
            "api_secret": self.api_secret,
            "api_passphrase": self.api_passphrase,
        }


class GeoblockError(RuntimeError):
    pass


async def check_geoblock(url: str, client: httpx.AsyncClient | None = None) -> dict:
    """Hard gate at startup.

    The international exchange blocks *order placement* from ~33 countries;
    public data is unrestricted, so a bot in a blocked region will happily
    build a book and then fail every order. Fail loudly here instead.
    """
    owns = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    try:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
    finally:
        if owns:
            await client.aclose()
    if payload.get("blocked"):
        raise GeoblockError(
            f"order placement is geoblocked from {payload.get('country')}/"
            f"{payload.get('region')} — use the Polymarket US exchange "
            f"(docs.polymarket.us) or run from a permitted region"
        )
    log.info("geoblock: clear (%s)", payload.get("country"))
    return payload


def load_cached_creds(path: str | Path) -> ApiCreds | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text())
        return ApiCreds(**raw)
    except (json.JSONDecodeError, TypeError):
        log.warning("creds cache at %s is unreadable; re-deriving", p)
        return None


def save_creds(path: str | Path, creds: ApiCreds) -> None:
    p = Path(path)
    p.write_text(json.dumps(creds.to_dict(), indent=2))
    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)  # 0600 — these are trading keys


def build_client(cfg: BotConfig, secrets: Secrets):
    """Construct a signed ClobClient. Import is lazy so paper mode, tests and
    the backtester never need the SDK or a private key.
    """
    try:
        from py_clob_client_v2.client import ClobClient
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "py-clob-client-v2 is required for live mode: pip install py-clob-client-v2"
        ) from exc

    if cfg.signature_type not in SUPPORTED_SIGNATURE_TYPES:
        raise RuntimeError(
            f"signature_type {cfg.signature_type} is not supported by this bot "
            f"(supported: {sorted(SUPPORTED_SIGNATURE_TYPES)})"
        )
    if not secrets.private_key:
        raise RuntimeError("PRIVATE_KEY is not set; live mode cannot sign orders")

    return ClobClient(
        host=cfg.host,
        chain_id=cfg.chain_id,
        key=secrets.private_key,
        signature_type=cfg.signature_type,
        funder=secrets.funder_address or None,
    )


async def authenticate(cfg: BotConfig, secrets: Secrets):
    """Return (client, creds) ready for L2 trading requests."""
    client = await asyncio.to_thread(build_client, cfg, secrets)
    cached = load_cached_creds(secrets.creds_cache_path)
    if cached is not None:
        log.info("auth: using cached L2 credentials")
        creds = cached
    else:
        raw = await asyncio.to_thread(client.create_or_derive_api_key)
        creds = ApiCreds(
            api_key=raw.api_key, api_secret=raw.api_secret, api_passphrase=raw.api_passphrase
        )
        save_creds(secrets.creds_cache_path, creds)
        log.info("auth: derived and cached new L2 credentials")

    from py_clob_client_v2.clob_types import ApiCreds as SdkCreds

    await asyncio.to_thread(
        client.set_api_creds,
        SdkCreds(creds.api_key, creds.api_secret, creds.api_passphrase),
    )
    return client, creds


async def clock_skew_seconds(client) -> float:
    """Server time minus local time. Past ~5s the HMAC timestamps are rejected."""
    import time

    server_time = await asyncio.to_thread(client.get_server_time)
    return float(server_time) - time.time()
