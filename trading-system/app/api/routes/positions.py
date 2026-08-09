from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth import require_api_key
from app.db.base import get_session
from app.db.models import OrderRecord, TradeJournalRecord

router = APIRouter(prefix="/positions", tags=["positions"], dependencies=[Depends(require_api_key)])


@router.get("")
def open_positions(session: Session = Depends(get_session)) -> list[dict]:
    """Positions that are actually open right now.

    Derived from the durable log rather than the live execution adapter, so it
    still answers correctly while the broker link is down (Phase 8, item 2) —
    the journal row is written when the position opens and closed out when it
    closes, so `closed_at IS NULL` is the authoritative open set.

    This deliberately does NOT return every order that ever filled. An order's
    status stays "filled" forever, so filtering on it reports long-closed
    trades as live exposure — the one number on the dashboard that has to be
    right. Trade history lives at /journal.
    """
    rows = (
        session.query(TradeJournalRecord, OrderRecord)
        .join(OrderRecord, TradeJournalRecord.order_id == OrderRecord.id)
        .filter(TradeJournalRecord.closed_at.is_(None))
        .order_by(TradeJournalRecord.opened_at.desc())
        .all()
    )
    return [
        {
            "id": order.id,
            "instrument": trade.instrument,
            "asset_class": trade.asset_class.value,
            "strategy_id": trade.strategy_id,
            "direction": trade.direction.value,
            "size": trade.size,
            "filled_price": order.filled_price if order.filled_price is not None else trade.entry_price,
            "stop_loss": trade.stop_loss,
            "take_profit": trade.take_profit,
            "status": order.status,
            "created_at": order.created_at,
            "opened_at": trade.opened_at,
        }
        for trade, order in rows
    ]
