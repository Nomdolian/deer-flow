"""Kill switches. All mandatory, all latch until explicitly cleared.

The pattern is the daily-drawdown breaker from the MQL5 EAs: once tripped,
trading stops and stays stopped for a fixed window. Nothing auto-resumes on
a hunch that conditions improved.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

HALT_SECONDS = 24 * 3600


@dataclass
class Trip:
    reason: str
    ts: float
    until: float


@dataclass
class KillSwitch:
    trips: list[Trip] = field(default_factory=list)
    api_errors: list[float] = field(default_factory=list)
    on_trip: object = None  # Callable[[str], None] — Telegram alert

    def engage(self, reason: str, seconds: float = HALT_SECONDS, now: float | None = None) -> Trip:
        now = now if now is not None else time.time()
        trip = Trip(reason=reason, ts=now, until=now + seconds)
        self.trips.append(trip)
        log.error("KILL SWITCH: %s (halted until %.0f)", reason, trip.until)
        if callable(self.on_trip):
            self.on_trip(reason)
        return trip

    def active_trip(self, now: float | None = None) -> Trip | None:
        now = now if now is not None else time.time()
        live = [t for t in self.trips if t.until > now]
        return live[-1] if live else None

    def is_engaged(self, now: float | None = None) -> bool:
        return self.active_trip(now) is not None

    def clear(self) -> None:
        """Manual reset only — after a human has read the journal."""
        self.trips.clear()

    # ---- detectors --------------------------------------------------------

    def check_daily_drawdown(self, starting_equity: float, equity: float, limit_pct: float,
                             now: float | None = None) -> bool:
        if starting_equity <= 0:
            return False
        drawdown_pct = (starting_equity - equity) / starting_equity * 100.0
        if drawdown_pct >= limit_pct:
            self.engage(f"daily_drawdown_{drawdown_pct:.2f}pct", now=now)
            return True
        return False

    def check_consecutive_losses(self, losses: int, limit: int, now: float | None = None) -> bool:
        if limit > 0 and losses >= limit:
            self.engage(f"consecutive_losses_{losses}", now=now)
            return True
        return False

    def check_feed_gap(self, ws_age_s: float, limit_s: float, now: float | None = None) -> bool:
        """A disconnected feed means resting orders are quoting blind. This
        one halts for two minutes, not a day: it is a connectivity event and
        it clears itself when the socket comes back.
        """
        if ws_age_s > limit_s:
            self.engage(f"ws_gap_{ws_age_s:.0f}s", seconds=120, now=now)
            return True
        return False

    def check_clock_skew(self, skew_s: float, limit_s: float, now: float | None = None) -> bool:
        """HMAC timestamps fail past the server's tolerance anyway; halting
        makes the failure legible instead of a wall of 401s.
        """
        if abs(skew_s) > limit_s:
            self.engage(f"clock_skew_{skew_s:.1f}s", seconds=300, now=now)
            return True
        return False

    def record_api_error(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        self.api_errors.append(now)
        self.api_errors = [t for t in self.api_errors if now - t < 60.0]

    def error_rate_per_min(self, now: float | None = None) -> int:
        now = now if now is not None else time.time()
        return len([t for t in self.api_errors if now - t < 60.0])

    def check_error_rate(self, limit_per_min: int = 20, now: float | None = None) -> bool:
        if self.error_rate_per_min(now) >= limit_per_min:
            self.engage(f"api_error_rate_{self.error_rate_per_min(now)}/min", seconds=600, now=now)
            return True
        return False
