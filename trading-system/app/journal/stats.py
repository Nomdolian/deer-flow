from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import StrategyVersion, TradeJournalRecord
from app.logging_utils import log_decision

MIN_SAMPLE_SIZE = 20  # statistically meaningful sample before any mechanical action


@dataclass(frozen=True, slots=True)
class StrategyStats:
    strategy_id: str
    version: int
    instrument: str
    sample_size: int
    win_rate: float
    expectancy_r: float
    avg_r: float
    max_drawdown_r: float


def compute_stats(session: Session, strategy_id: str, version: int, instrument: str) -> StrategyStats | None:
    """Weekly (not real-time) statistical review job (Phase 5, item 3):
    recompute win rate, expectancy, average R, and max drawdown per
    strategy_id+version per instrument."""
    trades = (
        session.query(TradeJournalRecord)
        .filter(
            TradeJournalRecord.strategy_id == strategy_id,
            TradeJournalRecord.strategy_version == version,
            TradeJournalRecord.instrument == instrument,
            TradeJournalRecord.closed_at.isnot(None),
        )
        .order_by(TradeJournalRecord.closed_at)
        .all()
    )
    if not trades:
        return None

    r_values = [t.r_multiple for t in trades if t.r_multiple is not None]
    if not r_values:
        return None

    wins = [r for r in r_values if r > 0]
    win_rate = len(wins) / len(r_values)
    expectancy_r = sum(r_values) / len(r_values)

    running_max = 0.0
    cumulative = 0.0
    max_drawdown = 0.0
    for r in r_values:
        cumulative += r
        running_max = max(running_max, cumulative)
        max_drawdown = max(max_drawdown, running_max - cumulative)

    return StrategyStats(
        strategy_id=strategy_id,
        version=version,
        instrument=instrument,
        sample_size=len(r_values),
        win_rate=win_rate,
        expectancy_r=expectancy_r,
        avg_r=expectancy_r,
        max_drawdown_r=max_drawdown,
    )


def apply_mechanical_weighting(session: Session, stats: StrategyStats) -> str:
    """Hard-coded, mechanical strategy weighting (Phase 5, item 4) — NOT left to
    LLM judgment. Strategies whose live expectancy falls significantly below
    their backtested baseline over a meaningful sample get down-weighted or
    paused. Returns the action taken, for logging/testing."""
    strategy_version = (
        session.query(StrategyVersion)
        .filter_by(strategy_id=stats.strategy_id, version=stats.version)
        .one_or_none()
    )
    if strategy_version is None:
        return "unknown_strategy_version"

    if stats.sample_size < MIN_SAMPLE_SIZE:
        return "insufficient_sample"

    baseline = strategy_version.backtest_expectancy_r
    if baseline is None or baseline <= 0:
        return "no_baseline"

    ratio = stats.expectancy_r / baseline
    action = "expectancy_at_or_above_baseline"

    if ratio < 0.3:
        strategy_version.is_paused = True
        action = "paused_edge_degraded"
    elif ratio < 0.6:
        strategy_version.size_multiplier = 0.5
        action = "down_weighted_edge_degraded"
    elif ratio >= 0.9 and not strategy_version.is_paused:
        strategy_version.size_multiplier = 1.0
        action = "restored_full_size"

    session.commit()
    if action != "expectancy_at_or_above_baseline":
        log_decision(
            session,
            event_type="strategy_weighting_updated",
            agent_id="journal_stats",
            payload={
                "strategy_id": stats.strategy_id,
                "version": stats.version,
                "instrument": stats.instrument,
                "expectancy_r": stats.expectancy_r,
                "baseline_expectancy_r": baseline,
                "ratio": ratio,
                "action": action,
            },
        )
    return action
