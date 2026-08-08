from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth import require_api_key
from app.db.base import get_session
from app.db.models import OrderRecord

router = APIRouter(prefix="/positions", tags=["positions"], dependencies=[Depends(require_api_key)])


@router.get("")
def open_orders(session: Session = Depends(get_session)) -> list[dict]:
    """Read-only view of recent filled orders. A production dashboard would
    query the live execution adapter for true open-position state (Phase 8,
    item 2); this endpoint exposes what the durable log recorded, which is what
    the mobile/PC client apps need for a P&L feed independent of broker uptime.
    """
    orders = session.query(OrderRecord).order_by(OrderRecord.created_at.desc()).limit(100).all()
    return [
        {
            "id": o.id,
            "instrument": o.instrument,
            "direction": o.direction.value,
            "size": o.requested_size,
            "filled_price": o.filled_price,
            "stop_loss": o.stop_loss,
            "take_profit": o.take_profit,
            "status": o.status,
            "created_at": o.created_at,
        }
        for o in orders
    ]
