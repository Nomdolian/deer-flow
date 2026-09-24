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
Venue = Literal["international", "us"]


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
    # Candidates to consider. Discovery is manual (the public leaderboard);
    # the bars below decide which of them are actually followed.
    wallets: list[str] = Field(default_factory=list)
    min_hit_rate: float = 0.5
    min_closed_positions: int = 20
    max_idle_days: float = 90
    rerank_hours: float = 24


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


class ChainConfig(BaseModel):
    """Polygon access for the things the CLOB API cannot do: allowances,
    redemption, and split/merge. Only needed in live mode.
    """

    rpc_url: str = "https://polygon-rpc.com"
    # Empty means "take it from the SDK's own contract config", which is what
    # the order signer uses. Override only if your deployment is ahead of the
    # pinned SDK — and verify on a block explorer first.
    contracts: dict[str, str] = Field(default_factory=dict)
    auto_redeem: bool = True
    auto_merge_sets: bool = True
    # Redemption costs gas; below this the sweep is not worth the transaction.
    min_redeem_usd: float = 1.0
    # A dry run logs every transaction it would send and sends nothing. Turn
    # it off deliberately, once you have read a dry run.
    dry_run: bool = True


class MonitorConfig(BaseModel):
    telegram_enabled: bool = True
    heartbeat_minutes: int = 15
    daily_summary_utc: str = "22:00"
    health_port: int = 8787


# The US venue is a separate, CFTC-regulated exchange with its own hosts and
# a flat fee schedule. Selecting it rewrites the hosts below at load time.
US_HOSTS = {
    "host": "https://clob.polymarket.us",
    "geoblock_url": "https://polymarket.us/api/geoblock",
    "gamma_host": "https://gamma-api.polymarket.us",
    "data_host": "https://data-api.polymarket.us",
    "ws_host": "wss://ws-subscriptions-clob.polymarket.us",
}


class BotConfig(BaseModel):
    """The whole of bot.yaml, typed."""

    mode: Mode = "paper"
    # international: the global exchange, blocked from ~33 countries.
    # us: the CFTC-regulated venue, flat 0.05 taker with a maker rebate.
    venue: Venue = "international"
    host: str = "https://clob.polymarket.com"
    gamma_host: str = "https://gamma-api.polymarket.com"
    data_host: str = "https://data-api.polymarket.com"
    ws_host: str = "wss://ws-subscriptions-clob.polymarket.com"
    geoblock_url: str = "https://polymarket.com/api/geoblock"
    binance_ws: str = "wss://stream.binance.com:9443/ws"
    chain_id: int = 137
    signature_type: int = 0
    db_path: str = "pmbot.sqlite"
    # Simulated bankroll for paper mode. Live mode ignores it and reads the
    # real USDC balance from the chain. Set it to what you actually intend to
    # trade with — sizing is a percentage of equity, so a paper run at $50k
    # tells you nothing about how the bot behaves at $250.
    paper_starting_usdc: float = 500.0
    loop_interval_s: float = 1.0
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    chain: ChainConfig = Field(default_factory=ChainConfig)
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
    cfg = _apply_env_overrides(cfg)
    return _apply_venue(cfg, raw)


def _apply_venue(cfg: BotConfig, raw: dict[str, Any]) -> BotConfig:
    """Point the hosts at the selected venue unless they were set explicitly.

    A US-based operator cannot legally trade the international exchange, and
    the geoblock gate will stop them at startup; switching venue is the fix,
    not a workaround to be applied silently.
    """
    if cfg.venue != "us":
        return cfg
    for field_name, host in US_HOSTS.items():
        if field_name not in raw:
            setattr(cfg, field_name, host)
    return cfg


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
