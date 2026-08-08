from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth import require_api_key
from app.db.base import get_session
from app.db.models import RiskDecisionRecord, SignalRecord

router = APIRouter(prefix="/signals", tags=["signals"], dependencies=[Depends(require_api_key)])


@router.get("")
def recent_signals(session: Session = Depends(get_session)) -> list[dict]:
    signals = session.query(SignalRecord).order_by(SignalRecord.created_at.desc()).limit(100).all()
    result = []
    for s in signals:
        decision = (
            session.query(RiskDecisionRecord)
            .filter_by(signal_id=s.id)
            .order_by(RiskDecisionRecord.created_at.desc())
            .first()
        )
        result.append(
            {
                "id": s.id,
                "instrument": s.instrument,
                "strategy_id": s.strategy_id,
                "strategy_version": s.strategy_version,
                "direction": s.direction.value,
                "confidence_score": s.confidence_score,
                "confluences": s.confluences,
                "entry": s.entry,
                "stop_loss": s.stop_loss,
                "take_profit": s.take_profit,
                "created_at": s.created_at,
                "risk_decision": {"accepted": decision.accepted, "reason": decision.reason} if decision else None,
            }
        )
    return result
