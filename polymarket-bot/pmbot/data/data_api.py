"""Data API: positions, trades, and any wallet's history.

Public and unauthenticated — which is what makes S5 possible, and also what
makes your own wallet's activity public. Assume anyone can see your fills.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from pmbot.data.ratelimit import bucket_for
from pmbot.strategies.s5_copy import WalletFill

log = logging.getLogger(__name__)


class DataApiClient:
    def __init__(self, host: str, client: httpx.AsyncClient | None = None):
        self.host = host.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=20.0)
        self._owns_client = client is None
        self._trades = bucket_for("data_trades")
        self._positions = bucket_for("data_positions")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get(self, path: str, params: dict) -> Any:
        response = await self._client.get(f"{self.host}{path}", params=params)
        response.raise_for_status()
        return response.json()

    async def positions(self, wallet: str) -> list[dict]:
        await self._positions.acquire()
        raw = await self._get("/positions", {"user": wallet, "sizeThreshold": 0.1})
        return raw if isinstance(raw, list) else raw.get("data", [])

    async def trades(self, wallet: str, limit: int = 100) -> list[WalletFill]:
        await self._trades.acquire()
        raw = await self._get("/trades", {"user": wallet, "limit": limit})
        rows = raw if isinstance(raw, list) else raw.get("data", [])
        fills: list[WalletFill] = []
        for row in rows:
            try:
                fills.append(
                    WalletFill(
                        wallet=wallet,
                        token_id=str(row.get("asset") or row.get("asset_id") or ""),
                        side=str(row.get("side", "")).upper(),
                        price=float(row.get("price", 0)),
                        size=float(row.get("size", 0)),
                        ts=float(row.get("timestamp", 0)),
                        tx_hash=str(row.get("transactionHash") or ""),
                    )
                )
            except (TypeError, ValueError):
                continue
        return [f for f in fills if f.token_id]

    async def rank_wallets(self, wallets: list[str], min_pnl_usd: float) -> list[str]:
        """Keep only wallets whose *realised* PnL clears the bar.

        Ranking on open positions is how you end up copying someone who is
        one resolution away from being wiped out.
        """
        keepers: list[str] = []
        for wallet in wallets:
            try:
                rows = await self.positions(wallet)
            except httpx.HTTPError as exc:
                log.warning("data-api: positions failed for %s (%s)", wallet, exc)
                continue
            realized = sum(float(r.get("realizedPnl", 0) or 0) for r in rows)
            if realized >= min_pnl_usd:
                keepers.append(wallet)
        return keepers
