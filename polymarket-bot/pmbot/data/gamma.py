"""Gamma: market and event discovery. No auth, no cost."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import httpx

from pmbot.data.ratelimit import bucket_for
from pmbot.universe import Market

log = logging.getLogger(__name__)


def _parse_ts(value: Any) -> float:
    if not value:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return 0.0


def _as_list(value: Any) -> list:
    """Gamma returns some array fields as JSON-encoded strings."""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
    return []


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_market(raw: dict) -> list[Market]:
    """One Gamma market carries N outcome tokens; emit one Market each.

    Binary markets get complement_token_id filled in so S2 can price both
    legs without a second lookup.
    """
    token_ids = [str(t) for t in _as_list(raw.get("clobTokenIds"))]
    outcomes = [str(o) for o in _as_list(raw.get("outcomes"))]
    if not token_ids:
        return []

    events = raw.get("events") or []
    event = events[0] if events else {}
    tags = [str(t.get("slug") or t.get("label") or t) for t in (raw.get("tags") or event.get("tags") or [])]
    if not tags and raw.get("category"):
        tags = [str(raw["category"])]

    end_ts = _parse_ts(raw.get("endDate") or raw.get("end_date_iso"))
    neg_risk = raw.get("negRisk")
    liquidity = _float(raw.get("liquidityNum", raw.get("liquidity")))
    volume_24h = _float(raw.get("volume24hr", raw.get("volume24hrClob")))
    spread = raw.get("spread")

    markets: list[Market] = []
    for index, token_id in enumerate(token_ids):
        complement = token_ids[1 - index] if len(token_ids) == 2 else None
        markets.append(
            Market(
                token_id=token_id,
                condition_id=str(raw.get("conditionId") or ""),
                question=str(raw.get("question") or ""),
                outcome=outcomes[index] if index < len(outcomes) else "",
                event_id=str(event.get("id") or raw.get("eventId") or ""),
                event_slug=str(event.get("slug") or ""),
                tags=tags,
                neg_risk=bool(neg_risk) if neg_risk is not None else None,
                tick_size=_float(raw.get("orderPriceMinTickSize"), 0.01),
                min_order_size=_float(raw.get("orderMinSize"), 5.0),
                end_ts=end_ts,
                liquidity_usd=liquidity,
                volume_24h_usd=volume_24h,
                spread=_float(spread, 0.0) if spread is not None else None,
                resolution_criteria=str(raw.get("description") or ""),
                complement_token_id=complement,
                disputed=str(raw.get("umaResolutionStatus", "")).lower() == "disputed",
            )
        )
    return markets


class GammaClient:
    def __init__(self, host: str, client: httpx.AsyncClient | None = None):
        self.host = host.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=20.0)
        self._owns_client = client is None
        self._bucket = bucket_for("gamma_markets")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch_markets(self, limit: int = 500, page_size: int = 100) -> list[Market]:
        """Active, open markets. Paginated — Gamma caps page size well below
        the number of live markets.
        """
        out: list[Market] = []
        offset = 0
        while offset < limit:
            await self._bucket.acquire()
            params = {
                "active": "true",
                "closed": "false",
                "archived": "false",
                "limit": min(page_size, limit - offset),
                "offset": offset,
                "order": "liquidityNum",
                "ascending": "false",
            }
            response = await self._client.get(f"{self.host}/markets", params=params)
            response.raise_for_status()
            page = response.json()
            if isinstance(page, dict):
                page = page.get("data", [])
            if not page:
                break
            for raw in page:
                out.extend(parse_market(raw))
            offset += len(page)
        log.info("gamma: discovered %d outcome tokens", len(out))
        return out

    async def fetch_by_condition_id(self, condition_id: str) -> dict | None:
        """Raw market payload for one condition — used by the redeem worker to
        read resolution state (closed, outcomePrices, dispute status).
        """
        await self._bucket.acquire()
        response = await self._client.get(
            f"{self.host}/markets", params={"condition_ids": condition_id}
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            payload = payload.get("data", [])
        return payload[0] if payload else None
