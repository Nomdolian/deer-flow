"""Data API: positions, trades, and any wallet's history.

Public and unauthenticated — which is what makes S5 possible, and also what
makes your own wallet's activity public. Assume anyone can see your fills.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from pmbot.data.ratelimit import bucket_for
from pmbot.strategies.s5_copy import WalletFill

log = logging.getLogger(__name__)


@dataclass
class WalletStats:
    """What we can honestly say about a wallet from public data.

    Realised PnL only: ranking on open positions is how you end up copying
    someone who is one resolution away from being wiped out.
    """

    wallet: str
    realized_pnl: float = 0.0
    closed_positions: int = 0
    wins: int = 0
    open_exposure_usd: float = 0.0
    last_trade_ts: float = 0.0

    @property
    def hit_rate(self) -> float:
        return self.wins / self.closed_positions if self.closed_positions else 0.0

    @property
    def idle_days(self) -> float:
        if not self.last_trade_ts:
            return float("inf")
        return (time.time() - self.last_trade_ts) / 86400.0

    @property
    def score(self) -> float:
        """PnL discounted by hit rate: one lucky whale-sized win is not a record."""
        return self.realized_pnl * self.hit_rate


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

    async def wallet_stats(self, wallet: str) -> WalletStats | None:
        """Realised record for one wallet, from its public position history."""
        try:
            rows = await self.positions(wallet)
        except httpx.HTTPError as exc:
            log.warning("data-api: positions failed for %s (%s)", wallet, exc)
            return None

        stats = WalletStats(wallet=wallet)
        for row in rows:
            try:
                realized = float(row.get("realizedPnl", 0) or 0)
                size = float(row.get("size", 0) or 0)
                value = float(row.get("currentValue", 0) or 0)
            except (TypeError, ValueError):
                continue
            if size > 0:
                stats.open_exposure_usd += value
            # A position with realised PnL has been closed or redeemed at
            # least in part; that is the only part of the record that is real.
            if realized != 0.0:
                stats.closed_positions += 1
                stats.wins += 1 if realized > 0 else 0
                stats.realized_pnl += realized

        try:
            fills = await self.trades(wallet, limit=1)
            stats.last_trade_ts = fills[0].ts if fills else 0.0
        except httpx.HTTPError:
            stats.last_trade_ts = 0.0
        return stats

    async def rank_wallets(
        self,
        wallets: list[str],
        min_pnl_usd: float,
        min_hit_rate: float = 0.5,
        min_closed_positions: int = 20,
        max_idle_days: float = 90.0,
    ) -> list[WalletStats]:
        """Rank candidate wallets, dropping anyone who fails a bar.

        The bars are deliberately blunt: a big realised record, a hit rate
        better than a coin flip, enough closed positions for that to mean
        something, and recent activity. Wallet *discovery* is manual — put
        candidates in `strategies.s5_copy.wallets` — because there is no
        leaderboard endpoint this build can verify.
        """
        ranked: list[WalletStats] = []
        for wallet in wallets:
            stats = await self.wallet_stats(wallet)
            if stats is None:
                continue
            if stats.realized_pnl < min_pnl_usd:
                continue
            if stats.closed_positions < min_closed_positions:
                continue
            if stats.hit_rate < min_hit_rate:
                continue
            if stats.idle_days > max_idle_days:
                continue
            ranked.append(stats)
        ranked.sort(key=lambda s: -s.score)
        return ranked
