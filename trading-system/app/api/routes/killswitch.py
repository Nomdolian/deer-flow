from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth import require_api_key, require_totp
from app.db.base import get_session
from app.killswitch import service as killswitch

router = APIRouter(prefix="/killswitch", tags=["killswitch"], dependencies=[Depends(require_api_key)])


class EngageRequest(BaseModel):
    reason: str


@router.get("")
def get_state(session: Session = Depends(get_session)) -> dict:
    state = killswitch.get_state(session)
    return {
        "engaged": state.engaged,
        "reason": state.reason,
        "triggered_by": state.triggered_by,
        "engaged_at": state.engaged_at,
    }


@router.post("/engage")
def engage(body: EngageRequest, session: Session = Depends(get_session)) -> dict:
    # One-tap engage is intentionally NOT behind TOTP — halting new trades is
    # always safe to make easy. Only disengage requires the second factor.
    state = killswitch.engage(session, reason=body.reason, triggered_by="client_app")
    return {"engaged": state.engaged}


@router.post("/disengage", dependencies=[Depends(require_totp)])
def disengage(session: Session = Depends(get_session)) -> dict:
    state = killswitch.disengage(session, triggered_by="client_app")
    return {"engaged": state.engaged}
