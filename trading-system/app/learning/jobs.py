from sqlalchemy.orm import Session

from app.db.models import TradeJournalRecord
from app.journal.classification import classify_trade
from app.journal.stats import apply_mechanical_weighting, compute_stats
from app.llm_analyst.client import LLMAnalyst
from app.llm_analyst.mistake_report import generate_weekly_mistake_report
from app.logging_utils import log_decision


def run_classification_sweep(session: Session, analyst: LLMAnalyst, *, limit: int = 20) -> int:
    """Post-trade classification, run async after trades close — never on the
    execution path (Phase 5, item 2). Picks up any closed-but-unclassified
    trade, oldest first, capped per sweep to bound one run's LLM spend.
    Returns how many trades were attempted (classify_trade itself is
    best-effort and leaves classification unset on failure, so this count is
    "attempted," not "succeeded")."""
    trades = (
        session.query(TradeJournalRecord)
        .filter(TradeJournalRecord.closed_at.isnot(None), TradeJournalRecord.classification.is_(None))
        .order_by(TradeJournalRecord.closed_at)
        .limit(limit)
        .all()
    )
    for trade in trades:
        classify_trade(session, analyst, trade)
    return len(trades)


def run_weekly_review(session: Session) -> list[str]:
    """Mechanical strategy weighting (Phase 5, item 4) — hard-coded, not LLM
    judgment. Recomputes stats for every strategy_id+version+instrument
    combination with at least one closed trade and applies the down-weight/
    pause rule. Returns one human-readable line per combination reviewed."""
    combos = (
        session.query(
            TradeJournalRecord.strategy_id,
            TradeJournalRecord.strategy_version,
            TradeJournalRecord.instrument,
        )
        .filter(TradeJournalRecord.closed_at.isnot(None))
        .distinct()
        .all()
    )
    actions = []
    for strategy_id, version, instrument in combos:
        stats = compute_stats(session, strategy_id, version, instrument)
        if stats is None:
            continue
        action = apply_mechanical_weighting(session, stats)
        actions.append(f"{strategy_id} v{version} {instrument}: {action} (n={stats.sample_size})")
    return actions


def run_mistake_report(session: Session, analyst: LLMAnalyst, *, lookback_days: int = 7) -> str:
    """Weekly LLM mistake-pattern report (Phase 5, item 5 / Phase 6, item 1c):
    a recommendation surfaced for review, never an auto-applied rule change."""
    report = generate_weekly_mistake_report(session, analyst, lookback_days=lookback_days)
    log_decision(session, event_type="weekly_mistake_report", agent_id="llm_analyst", payload={"report": report})
    return report
