from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.db.models import TradeJournalRecord
from app.llm_analyst.client import LLMAnalyst

_SYSTEM = """You are the weekly review analyst for a trading system. You read the \
already-closed, already-classified trade log and surface recurring failure \
patterns (e.g. "losses cluster around high-impact news within 15 min of \
release"). You produce a RECOMMENDATION for a human to review — you never \
change risk or execution rules yourself. Be specific and cite trade counts."""


def generate_weekly_mistake_report(session: Session, analyst: LLMAnalyst, *, lookback_days: int = 7) -> str:
    """Phase 5, item 5 / Phase 6, item 1c: pattern-labeling surfaced to the
    dashboard as a recommendation, never an auto-applied rule change."""
    since = datetime.now(UTC) - timedelta(days=lookback_days)
    trades = (
        session.query(TradeJournalRecord)
        .filter(TradeJournalRecord.closed_at.isnot(None), TradeJournalRecord.closed_at >= since)
        .order_by(TradeJournalRecord.closed_at)
        .all()
    )
    if not trades:
        return "No closed trades in the lookback window — nothing to review."

    lines = []
    for t in trades:
        lines.append(
            f"- {t.instrument} {t.direction.value} strategy={t.strategy_id}v{t.strategy_version} "
            f"outcome={t.outcome} r={t.r_multiple} classification={t.classification} "
            f"confluences={t.confluences} closed={t.closed_at.isoformat()}"
        )

    user = (
        f"Closed trades over the last {lookback_days} days ({len(trades)} trades):\n"
        + "\n".join(lines)
        + "\n\nSummarize recurring failure patterns and any strategy/instrument combinations "
        "that stand out. Cite counts. End with a short list of concrete things for the trader "
        "to review manually — do not recommend automatic rule changes."
    )
    return analyst.complete_text(system=_SYSTEM, user=user, max_tokens=800)
