"""Free external price feed: Binance spot, for the crypto fair-value model.

No key, no cost, no rate limit that matters. Keeps a 1-minute return series
and an EWMA realised vol estimate — the sigma that S3's Phi(d2) needs.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from itertools import pairwise

import websockets

log = logging.getLogger(__name__)

MINUTES_PER_YEAR = 365.0 * 24.0 * 60.0


@dataclass
class SpotFeed:
    """One symbol's live price plus its realised vol estimate."""

    symbol: str
    lookback_minutes: int = 120
    ewma_lambda: float = 0.94
    last_price: float | None = None
    last_update_ts: float = 0.0
    minute_closes: deque = field(default_factory=lambda: deque(maxlen=240))
    _current_minute: int = 0

    def on_trade(self, price: float, ts: float | None = None) -> None:
        ts = ts if ts is not None else time.time()
        self.last_price = price
        self.last_update_ts = ts
        minute = int(ts // 60)
        if minute != self._current_minute:
            self.minute_closes.append((minute, price))
            self._current_minute = minute
        elif self.minute_closes:
            self.minute_closes[-1] = (minute, price)

    def annualised_vol(self, min_samples: int = 30) -> float | None:
        """EWMA of 1-minute log returns, annualised.

        Returns None below min_samples — a sigma from six observations is a
        random number, and S3 must not trade on one.
        """
        closes = [p for _, p in list(self.minute_closes)[-self.lookback_minutes - 1 :]]
        if len(closes) < min_samples + 1:
            return None
        returns = [math.log(b / a) for a, b in pairwise(closes) if a > 0 and b > 0]
        if len(returns) < min_samples:
            return None
        variance = returns[0] ** 2
        for r in returns[1:]:
            variance = self.ewma_lambda * variance + (1 - self.ewma_lambda) * r * r
        return math.sqrt(variance * MINUTES_PER_YEAR)

    def age_s(self, now: float | None = None) -> float:
        now = now if now is not None else time.time()
        return now - self.last_update_ts if self.last_update_ts else float("inf")


class BinanceFeed:
    def __init__(self, ws_host: str, symbols: list[str]):
        self.ws_host = ws_host.rstrip("/")
        self.feeds: dict[str, SpotFeed] = {s.lower(): SpotFeed(symbol=s.lower()) for s in symbols}
        self._task: asyncio.Task | None = None

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self._run(), name="binance_feed")
        return self._task

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def price(self, symbol: str) -> float | None:
        feed = self.feeds.get(symbol.lower())
        return feed.last_price if feed else None

    async def _run(self) -> None:
        streams = "/".join(f"{s}@trade" for s in self.feeds)
        url = f"{self.ws_host}/{streams}" if len(self.feeds) == 1 else f"{self.ws_host}/stream?streams={streams}"
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, close_timeout=5) as socket:
                    backoff = 1.0
                    log.info("binance: streaming %s", ", ".join(self.feeds))
                    async for raw in socket:
                        self._handle(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("binance: connection lost (%s); retry in %.0fs", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    def _handle(self, raw: str | bytes) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            return
        data = message.get("data", message)
        symbol = str(data.get("s", "")).lower()
        price = data.get("p")
        if not symbol or price is None:
            return
        feed = self.feeds.get(symbol)
        if feed is not None:
            trade_ts = data.get("T")
            feed.on_trade(float(price), ts=float(trade_ts) / 1000.0 if trade_ts else None)
