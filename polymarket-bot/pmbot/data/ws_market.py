"""CLOB market channel: ~100ms book, price and trade updates.

One websocket replaces thousands of REST polls. The rules that matter are
about what happens when it breaks: mark every book stale on disconnect,
reconnect with backoff, and re-snapshot over REST before applying another
delta. A gap you silently paper over is a book that quietly stops matching
reality.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable, Iterable

import websockets

from pmbot.data.book import OrderBook
from pmbot.data.clob_rest import ClobRestClient

log = logging.getLogger(__name__)

BookCallback = Callable[[OrderBook], Awaitable[None]]


class MarketFeed:
    def __init__(
        self,
        ws_host: str,
        rest: ClobRestClient,
        books: dict[str, OrderBook],
        on_update: BookCallback | None = None,
        ping_interval: float = 20.0,
    ):
        self.url = f"{ws_host.rstrip('/')}/ws/market"
        self.rest = rest
        self.books = books
        self.on_update = on_update
        self.ping_interval = ping_interval
        self.token_ids: list[str] = []
        self.last_message_ts: float = 0.0
        self.connected: bool = False
        self._resubscribe = asyncio.Event()
        self._task: asyncio.Task | None = None

    # ---- lifecycle --------------------------------------------------------

    def start(self, token_ids: Iterable[str]) -> asyncio.Task:
        self.token_ids = list(token_ids)
        self._task = asyncio.create_task(self._run(), name="ws_market")
        return self._task

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def set_universe(self, token_ids: Iterable[str]) -> None:
        """Universe refresh — reconnect so the subscription matches."""
        new = list(token_ids)
        if new != self.token_ids:
            self.token_ids = new
            self._resubscribe.set()

    def age_s(self, now: float | None = None) -> float:
        now = now if now is not None else time.time()
        return now - self.last_message_ts if self.last_message_ts else float("inf")

    # ---- internals --------------------------------------------------------

    async def _run(self) -> None:
        backoff = 1.0
        while True:
            try:
                await self._resnapshot()
                async with websockets.connect(
                    self.url, ping_interval=self.ping_interval, close_timeout=5
                ) as socket:
                    await socket.send(
                        json.dumps({"assets_ids": self.token_ids, "type": "market"})
                    )
                    self.connected = True
                    self.last_message_ts = time.time()
                    backoff = 1.0
                    log.info("ws_market: subscribed to %d tokens", len(self.token_ids))
                    await self._consume(socket)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any failure means reconnect
                log.warning("ws_market: connection lost (%s); retry in %.0fs", exc, backoff)
            finally:
                self.connected = False
                self._mark_all_stale()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _consume(self, socket) -> None:
        while True:
            if self._resubscribe.is_set():
                self._resubscribe.clear()
                return  # drop out so _run reconnects with the new universe
            try:
                raw = await asyncio.wait_for(socket.recv(), timeout=self.ping_interval * 2)
            except TimeoutError:
                log.warning("ws_market: no data for %.0fs; reconnecting", self.ping_interval * 2)
                return
            self.last_message_ts = time.time()
            await self._handle(raw)

    async def _handle(self, raw: str | bytes) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        messages = payload if isinstance(payload, list) else [payload]
        for message in messages:
            if not isinstance(message, dict):
                continue
            book = await self._apply(message)
            if book is not None and self.on_update is not None:
                await self.on_update(book)

    async def _apply(self, message: dict) -> OrderBook | None:
        event = message.get("event_type") or message.get("type")
        token_id = str(message.get("asset_id") or message.get("token_id") or "")
        if not token_id:
            return None
        book = self.books.get(token_id)
        if book is None:
            book = OrderBook(token_id=token_id)
            self.books[token_id] = book

        timestamp = message.get("timestamp")
        ts = float(timestamp) / 1000.0 if timestamp else None

        if event == "book":
            book.apply_snapshot(message.get("bids") or message.get("buys") or [],
                                message.get("asks") or message.get("sells") or [],
                                ts=ts, book_hash=message.get("hash"))
            return book
        if event == "price_change":
            changes = message.get("changes") or message.get("price_changes") or []
            if book.needs_resnapshot:
                await self._resnapshot([token_id])
                return book
            book.apply_price_change(changes, ts=ts, book_hash=message.get("hash"))
            return book
        if event == "tick_size_change":
            new_tick = message.get("new_tick_size")
            if new_tick:
                book.tick_size = float(new_tick)
            return book
        if event == "last_trade_price":
            price = message.get("price")
            if price is not None:
                book.last_trade_price = float(price)
            return book
        return None

    async def _resnapshot(self, token_ids: list[str] | None = None) -> None:
        targets = token_ids if token_ids is not None else self.token_ids
        if not targets:
            return
        fresh = await self.rest.get_books(targets)
        for token_id, book in fresh.items():
            existing = self.books.get(token_id)
            if existing is not None:
                book.last_trade_price = book.last_trade_price or existing.last_trade_price
            self.books[token_id] = book
        log.info("ws_market: re-snapshotted %d books", len(fresh))

    def _mark_all_stale(self) -> None:
        for book in self.books.values():
            book.mark_stale()
