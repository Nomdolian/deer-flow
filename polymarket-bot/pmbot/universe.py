"""Market universe: turn everything Polymarket lists into the 20-60 tokens
worth watching.

Most of the exchange is untradeable for a bot — no depth, months to
resolution, or resolution wording an oracle can read three ways. Filter hard
here, before any strategy sees a token, because every later stage costs
either latency or money.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from pmbot.config import UniverseConfig


@dataclass
class Market:
    """One tradeable outcome token, plus the metadata the risk and execution
    layers need. Sourced from Gamma and enriched from CLOB.
    """

    token_id: str
    condition_id: str
    question: str
    outcome: str = ""
    event_id: str = ""
    event_slug: str = ""
    tags: list[str] = field(default_factory=list)
    neg_risk: bool | None = None
    tick_size: float = 0.01
    min_order_size: float = 5.0
    end_ts: float = 0.0
    liquidity_usd: float = 0.0
    volume_24h_usd: float = 0.0
    spread: float | None = None
    resolution_criteria: str = ""
    # The other side of a binary market — S2 needs both legs.
    complement_token_id: str | None = None
    # Set by Gamma when UMA resolution is contested. Never add to a disputed market.
    disputed: bool = False
    updated_ts: float = field(default_factory=time.time)

    @property
    def category(self) -> str:
        return self.tags[0].lower() if self.tags else "other"

    def hours_to_resolution(self, now: float | None = None) -> float:
        now = now if now is not None else time.time()
        return (self.end_ts - now) / 3600.0


def _has_vague_wording(market: Market, phrases: list[str]) -> bool:
    text = market.resolution_criteria.lower()
    return any(p.lower() in text for p in phrases)


def filter_markets(
    markets: list[Market], cfg: UniverseConfig, now: float | None = None
) -> tuple[list[Market], dict[str, str]]:
    """Return (watchlist, {token_id: reject_reason}).

    Rejections are returned rather than dropped so the journal can show which
    filter is starving the bot of candidates.
    """
    now = now if now is not None else time.time()
    accepted: list[Market] = []
    rejected: dict[str, str] = {}

    for market in markets:
        reason = _reject_reason(market, cfg, now)
        if reason:
            rejected[market.token_id] = reason
        else:
            accepted.append(market)

    preferred = {t.lower() for t in cfg.prefer_tags}
    accepted.sort(
        key=lambda m: (
            0 if preferred & {t.lower() for t in m.tags} else 1,
            -m.liquidity_usd,
        )
    )
    if len(accepted) > cfg.max_markets:
        for market in accepted[cfg.max_markets :]:
            rejected[market.token_id] = "over_max_markets"
        accepted = accepted[: cfg.max_markets]
    return accepted, rejected


def _reject_reason(market: Market, cfg: UniverseConfig, now: float) -> str | None:
    if market.disputed:
        return "resolution_disputed"
    if market.liquidity_usd < cfg.min_liquidity_usd:
        return "below_min_liquidity"
    if market.volume_24h_usd < cfg.min_24h_volume_usd:
        return "below_min_volume"
    if market.spread is not None and market.spread * 100.0 > cfg.max_spread_cents:
        return "spread_too_wide"
    hours = market.hours_to_resolution(now)
    if hours < cfg.min_hours_to_resolution:
        return "resolves_too_soon"
    if hours > cfg.max_days_to_resolution * 24.0:
        return "resolves_too_late"
    tags = {t.lower() for t in market.tags}
    if tags & {t.lower() for t in cfg.exclude_tags}:
        return "excluded_tag"
    if cfg.require_negrisk_known and market.neg_risk is None:
        return "negrisk_unknown"
    if _has_vague_wording(market, cfg.exclude_resolution_phrases):
        return "vague_resolution_criteria"
    return None
