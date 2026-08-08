from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth import require_api_key
from app.db.base import get_session
from app.db.models import TradeJournalRecord

router = APIRouter(prefix="/journal", tags=["journal"], dependencies=[Depends(require_api_key)])


@router.get("")
def trade_journal(
    strategy_id: str | None = None,
    instrument: str | None = None,
    session: Session = Depends(get_session),
) -> list[dict]:
    query = session.query(TradeJournalRecord)
    if strategy_id:
        query = query.filter(TradeJournalRecord.strategy_id == strategy_id)
    if instrument:
        query = query.filter(TradeJournalRecord.instrument == instrument)
    trades = query.order_by(TradeJournalRecord.opened_at.desc()).limit(200).all()
    return [
        {
            "id": t.id,
            "instrument": t.instrument,
            "strategy_id": t.strategy_id,
            "strategy_version": t.strategy_version,
            "direction": t.direction.value,
            "confluences": t.confluences,
            "size": t.size,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "r_multiple": t.r_multiple,
            "pnl": t.pnl,
            "outcome": t.outcome,
            "classification": t.classification,
            "opened_at": t.opened_at,
            "closed_at": t.closed_at,
        }
        for t in trades
    ]
