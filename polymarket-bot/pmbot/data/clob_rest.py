"""CLOB public reads: books, prices, tick sizes, history.

Reads need no auth. This client is used for the bootstrap snapshot, for the
re-snapshot after a WSS gap, and for the 60s order reconciliation — never as
a polling substitute for the websocket.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from pmbot.data.book import OrderBook
from pmbot.data.ratelimit import bucket_for

log = logging.getLogger(__name__)


class ClobRestClient:
    def __init__(self, host: str, client: httpx.AsyncClient | None = None):
        self.host = host.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=20.0)
        self._owns_client = client is None
        self._books_bucket = bucket_for("clob_books")
        self._price_bucket = bucket_for("clob_price")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get(self, path: str, params: dict | None = None) -> Any:
        response = await self._client.get(f"{self.host}{path}", params=params)
        response.raise_for_status()
        return response.json()

    async def _post(self, path: str, payload: Any) -> Any:
        response = await self._client.post(f"{self.host}{path}", json=payload)
        response.raise_for_status()
        return response.json()

    async def get_book(self, token_id: str) -> OrderBook:
        await self._price_bucket.acquire()
        raw = await self._get("/book", {"token_id": token_id})
        return self._to_book(token_id, raw)

    async def get_books(self, token_ids: list[str], chunk: int = 50) -> dict[str, OrderBook]:
        """Batched snapshot. One call per chunk keeps the bootstrap inside the
        /books budget even with a 60-token watchlist.
        """
        books: dict[str, OrderBook] = {}
        for start in range(0, len(token_ids), chunk):
            batch = token_ids[start : start + chunk]
            await self._books_bucket.acquire()
            payload = [{"token_id": token_id} for token_id in batch]
            try:
                raw_books = await self._post("/books", payload)
            except httpx.HTTPError as exc:
                log.warning("books batch failed (%s); falling back to singles", exc)
                for token_id in batch:
                    try:
                        books[token_id] = await self.get_book(token_id)
                    except httpx.HTTPError:
                        log.warning("book fetch failed for %s", token_id)
                continue
            for raw in raw_books or []:
                token_id = str(raw.get("asset_id") or raw.get("token_id") or "")
                if token_id:
                    books[token_id] = self._to_book(token_id, raw)
        return books

    @staticmethod
    def _to_book(token_id: str, raw: dict) -> OrderBook:
        book = OrderBook(token_id=token_id)
        if raw.get("tick_size"):
            book.tick_size = float(raw["tick_size"])
        if raw.get("min_order_size"):
            book.min_order_size = float(raw["min_order_size"])
        timestamp = raw.get("timestamp")
        ts = float(timestamp) / 1000.0 if timestamp else None
        book.apply_snapshot(raw.get("bids") or [], raw.get("asks") or [], ts=ts,
                            book_hash=raw.get("hash"))
        if raw.get("last_trade_price"):
            book.last_trade_price = float(raw["last_trade_price"])
        return book

    async def get_tick_size(self, token_id: str) -> float:
        raw = await self._get("/tick-size", {"token_id": token_id})
        return float(raw.get("minimum_tick_size", 0.01))

    async def get_neg_risk(self, token_id: str) -> bool:
        raw = await self._get("/neg-risk", {"token_id": token_id})
        return bool(raw.get("neg_risk", False))

    async def get_midpoint(self, token_id: str) -> float | None:
        await self._price_bucket.acquire()
        raw = await self._get("/midpoint", {"token_id": token_id})
        value = raw.get("mid")
        return float(value) if value is not None else None

    async def get_prices_history(
        self, token_id: str, start_ts: int | None = None, end_ts: int | None = None,
        interval: str | None = None, fidelity: int | None = None
    ) -> list[tuple[int, float]]:
        """Free historical series for backtest replay. Mid/last-trade only —
        no book depth, so maker backtests off this data flatter themselves.
        """
        params: dict[str, Any] = {"market": token_id}
        if start_ts is not None:
            params["startTs"] = start_ts
        if end_ts is not None:
            params["endTs"] = end_ts
        if interval is not None:
            params["interval"] = interval
        if fidelity is not None:
            params["fidelity"] = fidelity
        raw = await self._get("/prices-history", params)
        return [(int(p["t"]), float(p["p"])) for p in raw.get("history", [])]

    async def get_fee_rates(self) -> dict[str, float] | None:
        """Live fee schedule, if the deployment's API version serves it.

        Returns None rather than raising: a missing endpoint must not stop the
        bot, it must only stop us from claiming the rates are fresh.
        """
        try:
            raw = await self._get("/fee-rate-bps")
        except httpx.HTTPError:
            return None
        if not isinstance(raw, dict):
            return None
        rates = raw.get("rates", raw)
        try:
            return {str(k).lower(): float(v) / 10_000.0 for k, v in rates.items()}
        except (TypeError, ValueError):
            return None
