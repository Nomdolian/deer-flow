"""Per-host token bucket.

Cloudflare throttles rather than hard-rejecting, so an over-eager poller
degrades into latency you cannot see. The published limits are far above
anything this bot needs — the bucket exists to keep a retry storm from
turning into one.
"""

from __future__ import annotations

import asyncio
import time


class TokenBucket:
    def __init__(self, rate_per_sec: float, capacity: float | None = None):
        self.rate = rate_per_sec
        self.capacity = capacity if capacity is not None else rate_per_sec
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                await asyncio.sleep((tokens - self._tokens) / self.rate)

    def try_acquire(self, tokens: float = 1.0) -> bool:
        now = time.monotonic()
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
        self._last = now
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False


# Published limits (per 10s) with a wide safety margin applied.
LIMITS: dict[str, float] = {
    "gamma_markets": 20.0,
    "gamma_events": 30.0,
    "clob_book": 100.0,
    "clob_books": 30.0,
    "clob_price": 100.0,
    "data_trades": 15.0,
    "data_positions": 10.0,
    "order": 50.0,
}


def bucket_for(name: str) -> TokenBucket:
    return TokenBucket(LIMITS.get(name, 10.0))
