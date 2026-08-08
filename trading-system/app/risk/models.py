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
    size: float | None = None
    risk_amount: float | None = None
    portfolio_risk_after_pct: float | None = None
    correlation_group: str | None = None
