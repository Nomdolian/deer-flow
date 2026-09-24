"""CLOB user channel: your fills and cancels, L2-authenticated.

This is the fast path for order state. It is not the authoritative one —
messages get dropped, so execution/registry.py reconciles against
GET /orders on a timer regardless of what arrives here.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable

import websockets

log = logging.getLogger(__name__)

EventCallback = Callable[[dict], Awaitable[None]]


class UserFeed:
    def __init__(
        self,
        ws_host: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        on_event: EventCallback,
        markets: list[str] | None = None,
        ping_interval: float = 20.0,
    ):
        self.url = f"{ws_host.rstrip('/')}/ws/user"
        self.auth = {"apiKey": api_key, "secret": api_secret, "passphrase": api_passphrase}
        self.on_event = on_event
        self.markets = markets or []
        self.ping_interval = ping_interval
        self.last_message_ts: float = 0.0
        self.connected = False
        self._task: asyncio.Task | None = None

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self._run(), name="ws_user")
        return self._task

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(
                    self.url, ping_interval=self.ping_interval, close_timeout=5
                ) as socket:
                    await socket.send(
                        json.dumps({"auth": self.auth, "markets": self.markets, "type": "user"})
                    )
                    self.connected = True
                    self.last_message_ts = time.time()
                    backoff = 1.0
                    log.info("ws_user: authenticated")
                    async for raw in socket:
                        self.last_message_ts = time.time()
                        await self._handle(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("ws_user: connection lost (%s); retry in %.0fs", exc, backoff)
            finally:
                self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _handle(self, raw: str | bytes) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        messages = payload if isinstance(payload, list) else [payload]
        for message in messages:
            if isinstance(message, dict) and message.get("event_type") in {"trade", "order"}:
                await self.on_event(message)
