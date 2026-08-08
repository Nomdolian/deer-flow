import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid.uuid4())


class AssetClass(str, enum.Enum):
    forex = "forex"
    indices = "indices"
    metals = "metals"
    commodities = "commodities"
    crypto_major = "crypto_major"
    crypto_meme = "crypto_meme"
    stocks = "stocks"


class Direction(str, enum.Enum):
    long = "long"
    short = "short"


class StrategyVersion(Base):
    """Every parameter change bumps a new row so live performance can be attributed
    to the exact strategy version that produced it (required for Phase 5 weighting)."""

    __tablename__ = "strategy_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    strategy_id: Mapped[str] = mapped_column(String, index=True)
    version: Mapped[int] = mapped_column(Integer)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    asset_class: Mapped[AssetClass] = mapped_column(Enum(AssetClass))
    backtest_win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    backtest_expectancy_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    backtest_max_drawdown_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    consecutive_losses: Mapped[int] = mapped_column(Integer, default=0)
    size_multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SignalRecord(Base):
    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    strategy_id: Mapped[str] = mapped_column(String, index=True)
    strategy_version: Mapped[int] = mapped_column(Integer)
    instrument: Mapped[str] = mapped_column(String, index=True)
    asset_class: Mapped[AssetClass] = mapped_column(Enum(AssetClass))
    direction: Mapped[Direction] = mapped_column(Enum(Direction))
    confidence_score: Mapped[float] = mapped_column(Float)
    entry: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit: Mapped[float] = mapped_column(Float)
    confluences: Mapped[list] = mapped_column(JSON, default=list)
    candle_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RiskDecisionRecord(Base):
    """Every accepted or rejected signal, and why. The Risk Manager is the single
    authoritative gate — nothing skips this log."""

    __tablename__ = "risk_decisions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    signal_id: Mapped[str] = mapped_column(String, ForeignKey("signals.id"), index=True)
    accepted: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(Text)
    approved_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    portfolio_risk_after_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    correlation_group: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class OrderRecord(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    client_order_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    signal_id: Mapped[str | None] = mapped_column(String, ForeignKey("signals.id"), nullable=True)
    instrument: Mapped[str] = mapped_column(String, index=True)
    direction: Mapped[Direction] = mapped_column(Enum(Direction))
    requested_size: Mapped[float] = mapped_column(Float)
    requested_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    filled_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")
    broker: Mapped[str] = mapped_column(String)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TradeJournalRecord(Base):
    __tablename__ = "trade_journal"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    order_id: Mapped[str] = mapped_column(String, ForeignKey("orders.id"))
    strategy_id: Mapped[str] = mapped_column(String, index=True)
    strategy_version: Mapped[int] = mapped_column(Integer)
    instrument: Mapped[str] = mapped_column(String, index=True)
    asset_class: Mapped[AssetClass] = mapped_column(Enum(AssetClass))
    direction: Mapped[Direction] = mapped_column(Enum(Direction))
    confluences: Mapped[list] = mapped_column(JSON, default=list)
    size: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit: Mapped[float] = mapped_column(Float)
    r_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)  # win/loss/breakeven
    classification: Mapped[str | None] = mapped_column(String, nullable=True)
    classification_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KillSwitchState(Base):
    """Single-row table (id='global') holding the authoritative kill switch state."""

    __tablename__ = "kill_switch_state"

    id: Mapped[str] = mapped_column(String, primary_key=True, default="global")
    engaged: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(String, nullable=True)
    engaged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class FeedHealthRecord(Base):
    __tablename__ = "feed_health"

    instrument: Mapped[str] = mapped_column(String, primary_key=True)
    last_candle_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)


class DecisionLog(Base):
    """Structured append-only log: every decision (signal fired, risk check
    passed/failed, order sent, order filled/rejected). This log IS the memory
    system — durable, queryable, and written before-the-fact, not reconstructed."""

    __tablename__ = "decision_log"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    event_type: Mapped[str] = mapped_column(String, index=True)
    agent_id: Mapped[str] = mapped_column(String, index=True)
    instrument: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class DeviceRecord(Base):
    """A registered mobile client (Phase 8): holds the Expo push token used to
    deliver notifications for new high-confidence signals, position opened/
    closed, daily-loss-limit-approaching, kill-switch-triggered, and feed-
    health events. The client app is a control surface, never an execution
    host — this table has no bearing on trading logic."""

    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    push_token: Mapped[str] = mapped_column(String, unique=True, index=True)
    platform: Mapped[str] = mapped_column(String)  # "ios" | "android"
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
