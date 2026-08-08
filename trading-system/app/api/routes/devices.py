from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth import require_api_key
from app.db.base import get_session
from app.notifications.service import register_device, unregister_device

router = APIRouter(prefix="/devices", tags=["devices"], dependencies=[Depends(require_api_key)])


class RegisterDeviceRequest(BaseModel):
    push_token: str
    platform: str  # "ios" | "android"
    label: str | None = None


class UnregisterDeviceRequest(BaseModel):
    push_token: str


@router.post("/register")
def register(body: RegisterDeviceRequest, session: Session = Depends(get_session)) -> dict:
    device = register_device(session, body.push_token, body.platform, body.label)
    return {"id": device.id, "registered_at": device.registered_at}


@router.post("/unregister")
def unregister(body: UnregisterDeviceRequest, session: Session = Depends(get_session)) -> dict:
    unregister_device(session, body.push_token)
    return {"ok": True}
