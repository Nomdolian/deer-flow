from dataclasses import dataclass

from app.db.models import AssetClass


@dataclass(frozen=True, slots=True)
class OpenPosition:
    instrument: str
    asset_class: AssetClass
    strategy_id: str
    risk_amount: float  # account-currency $ at risk = |entry - stop| * size, always >= 0
    correlation_group: str


@dataclass(frozen=True, slots=True)
class PortfolioState:
    equity: float
    open_positions: list[OpenPosition]
    daily_realized_pnl: float
    daily_starting_equity: float
    weekly_realized_pnl: float
    weekly_starting_equity: float


@dataclass(frozen=True, slots=True)
class RiskCheckResult:
    accepted: bool
    reason: str
    # `size` is in instrument UNITS (base-currency units, ounces, coins, shares,
    # contracts) — the convention documented in app/risk/instruments.py, under
    # which money moved = price movement x size x point_value.
    size: float | None = None
    risk_amount: float | None = None
    portfolio_risk_after_pct: float | None = None
    correlation_group: str | None = None
    # Contract-spec provenance, so a decision can be audited after the fact.
    size_unit: str | None = None
    # Same position expressed in broker lots, for lot-based APIs such as MT5.
    size_lots: float | None = None
    point_value: float | None = None
    # True when no exact spec existed and an asset-class default was used.
    spec_is_class_default: bool | None = None
