"""Typed configuration: bot.yaml for behaviour, .env for secrets.

Anything that changes how much money moves lives in bot.yaml so it is
diffable and reviewable. Anything that would compromise the wallet if
leaked lives in .env and is never written back out.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Mode = Literal["paper", "live"]


class UniverseConfig(BaseModel):
    min_liquidity_usd: float = 25_000
    min_24h_volume_usd: float = 10_000
    max_spread_cents: float = 3
    min_hours_to_resolution: float = 2
    max_days_to_resolution: float = 45
    exclude_tags: list[str] = Field(default_factory=lambda: ["mentions"])
    prefer_tags: list[str] = Field(default_factory=lambda: ["geopolitics"])
    # Vague resolution wording is the #2 failure mode: blacklist on substring hit.
    exclude_resolution_phrases: list[str] = Field(
        default_factory=lambda: ["at the discretion", "generally accepted", "consensus of"]
    )
    require_negrisk_known: bool = True
    max_markets: int = 60
    refresh_seconds: int = 900


class MakerConfig(BaseModel):
    enabled: bool = True
    quote_size_usd: float = 20
    max_inventory_usd: float = 100
    requote_ticks: int = 1
    quote_max_age_s: float = 30
    half_spread_ticks: int = 1
    # Adverse selection defence: stop quoting into resolution and into spikes.
    pull_minutes_before_resolution: float = 30
    volume_spike_multiple: float = 4.0


class ArbConfig(BaseModel):
    enabled: bool = True
    min_edge_cents: float = 1.5
    max_legs: int = 8
    max_notional_usd: float = 250


class FairValueConfig(BaseModel):
    enabled: bool = False
    min_edge_cents: float = 5
    vol_lookback_min: int = 120
    min_samples: int = 30
    max_minutes_to_resolution: float = 120


class LongshotConfig(BaseModel):
    enabled: bool = False
    price_band: tuple[float, float] = (0.02, 0.06)
    max_hours: float = 48
    max_loss_per_event_usd: float = 25


class CopyConfig(BaseModel):
    enabled: bool = False
    min_wallet_pnl_usd: float = 50_000
    max_latency_cents: float = 2
    size_scalar: float = 0.05
    wallets: list[str] = Field(default_factory=list)


class StrategiesConfig(BaseModel):
    s1_maker: MakerConfig = Field(default_factory=MakerConfig)
    s2_arb: ArbConfig = Field(default_factory=ArbConfig)
    s3_fairvalue: FairValueConfig = Field(default_factory=FairValueConfig)
    s4_longshot: LongshotConfig = Field(default_factory=LongshotConfig)
    s5_copy: CopyConfig = Field(default_factory=CopyConfig)


class RiskConfig(BaseModel):
    max_position_pct_per_market: float = 2
    max_exposure_pct_per_event: float = 5
    max_total_deployed_pct: float = 40
    max_open_orders: int = 30
    daily_loss_kill_pct: float = 3
    max_consecutive_losses: int = 6
    min_free_usdc: float = 25
    per_strategy_cap_pct: dict[str, float] = Field(
        default_factory=lambda: {
            "s1_maker": 20,
            "s2_arb": 15,
            "s3_fairvalue": 10,
            "s4_longshot": 3,
            "s5_copy": 5,
        }
    )
    scale_with_equity: bool = True
    kelly_fraction: float = 0.25
    fee_adjusted_edge_min_cents: float = 1.5
    # A maker pays no fee and earns a rebate, so the taker floor does not
    # apply to it: its business is capturing half a cent of spread many
    # times. Predictive strategies keep the wide floor.
    edge_floor_cents_by_strategy: dict[str, float] = Field(
        default_factory=lambda: {"s1_maker": 0.5}
    )
    stale_book_max_ms: int = 1500
    max_clock_skew_s: float = 5
    ws_disconnect_flatten_s: float = 30
    require_geoblock_pass: bool = True
    min_edge_per_day_cents: float = 0.05


class MonitorConfig(BaseModel):
    telegram_enabled: bool = True
    heartbeat_minutes: int = 15
    daily_summary_utc: str = "22:00"
    health_port: int = 8787


class BotConfig(BaseModel):
    """The whole of bot.yaml, typed."""

    mode: Mode = "paper"
    host: str = "https://clob.polymarket.com"
    gamma_host: str = "https://gamma-api.polymarket.com"
    data_host: str = "https://data-api.polymarket.com"
    ws_host: str = "wss://ws-subscriptions-clob.polymarket.com"
    geoblock_url: str = "https://polymarket.com/api/geoblock"
    binance_ws: str = "wss://stream.binance.com:9443/ws"
    chain_id: int = 137
    signature_type: int = 0
    db_path: str = "pmbot.sqlite"
    loop_interval_s: float = 1.0
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    strategies: StrategiesConfig = Field(default_factory=StrategiesConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    monitor: MonitorConfig = Field(default_factory=MonitorConfig)


class Secrets(BaseSettings):
    """.env only. Never logged, never persisted, never echoed."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    private_key: str = ""
    funder_address: str = ""
    tg_token: str = ""
    tg_chat: str = ""
    creds_cache_path: str = ".creds.json"


def load_config(path: str | Path = "bot.yaml") -> BotConfig:
    """Load bot.yaml. Missing file is fine — the defaults above are the
    starter config, and every field is env-overridable via PMBOT_* for
    container deployments (PMBOT_MODE=live, PMBOT_RISK__KELLY_FRACTION=0.1).
    """
    raw: dict[str, Any] = {}
    p = Path(path)
    if p.exists():
        raw = yaml.safe_load(p.read_text()) or {}
    cfg = BotConfig.model_validate(raw)
    return _apply_env_overrides(cfg)


def _apply_env_overrides(cfg: BotConfig) -> BotConfig:
    data = cfg.model_dump()
    for key, value in os.environ.items():
        if not key.startswith("PMBOT_"):
            continue
        path = key[len("PMBOT_") :].lower().split("__")
        node: Any = data
        for part in path[:-1]:
            if not isinstance(node, dict) or part not in node:
                node = None
                break
            node = node[part]
        if isinstance(node, dict) and path[-1] in node:
            node[path[-1]] = yaml.safe_load(value)
    return BotConfig.model_validate(data)
