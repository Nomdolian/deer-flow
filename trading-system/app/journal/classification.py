from sqlalchemy.orm import Session

from app.db.models import TradeJournalRecord
from app.llm_analyst.client import LLMAnalyst

CLASSIFICATION_LABELS = (
    "valid-setup-normal-variance",
    "poor-entry-timing",
    "ignored-risk-rule",
    "news-event-invalidation",
    "correlated-overexposure",
    "strategy-edge-degraded",
)

_SYSTEM = f"""You classify a single closed trade against its logged signal reasoning \
and market context. Respond with a JSON object: {{"classification": "<one of {list(CLASSIFICATION_LABELS)}>", \
"notes": "<one sentence>"}}. Pick exactly one label. This classification is stored for \
review — it is never used to automatically change risk or execution rules."""


def classify_trade(session: Session, analyst: LLMAnalyst, trade: TradeJournalRecord) -> None:
    """Run async, after the trade closes — never on the execution path (Phase 5,
    item 2). Failure here must not affect the already-closed trade's PnL record;
    on any error, leave classification unset for later retry rather than guess."""
    user = (
        f"Instrument: {trade.instrument}\nDirection: {trade.direction.value}\n"
        f"Strategy: {trade.strategy_id} v{trade.strategy_version}\n"
        f"Confluences at entry: {trade.confluences}\n"
        f"Entry: {trade.entry_price}  Exit: {trade.exit_price}\n"
        f"Stop loss: {trade.stop_loss}  Take profit: {trade.take_profit}\n"
        f"R multiple achieved: {trade.r_multiple}\n"
        f"Outcome: {trade.outcome}\n"
    )
    try:
        result = analyst.complete_json(system=_SYSTEM, user=user, max_tokens=200)
        label = result.get("classification")
        if label not in CLASSIFICATION_LABELS:
            return  # fail closed: don't store an out-of-vocabulary classification
        trade.classification = label
        trade.classification_notes = result.get("notes")
        session.commit()
    except Exception:
        # Classification is best-effort review context, not risk-critical — swallow
        # and leave unset rather than raise into a batch job that processes many trades.
        session.rollback()
