from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth import require_api_key
from app.db.base import get_session
from app.db.models import AssetClass
from app.watchlist import service

router = APIRouter(prefix="/watchlist", tags=["watchlist"], dependencies=[Depends(require_api_key)])


class AddInstrumentRequest(BaseModel):
    instrument: str
    asset_class: str
    timeframe: str = "D1"
    data_source: str = "alphavantage"
    enabled: bool = True


class SetEnabledRequest(BaseModel):
    enabled: bool


@router.get("")
def list_watchlist(session: Session = Depends(get_session)) -> list[dict]:
    return [
        {
            "id": row.id,
            "instrument": row.instrument,
            "asset_class": row.asset_class.value,
            "timeframe": row.timeframe,
            "data_source": row.data_source,
            "enabled": row.enabled,
            "updated_at": row.updated_at,
        }
        for row in service.list_watchlist(session)
    ]


@router.post("")
def add_instrument(body: AddInstrumentRequest, session: Session = Depends(get_session)) -> dict:
    try:
        asset_class = AssetClass(body.asset_class)
    except ValueError as exc:
        raise HTTPException(422, f"unknown asset_class {body.asset_class!r}") from exc

    row = service.add_instrument(
        session,
        instrument=body.instrument,
        asset_class=asset_class,
        timeframe=body.timeframe,
        data_source=body.data_source,
        enabled=body.enabled,
    )
    return {"id": row.id, "instrument": row.instrument, "enabled": row.enabled}


@router.post("/{instrument}/enabled")
def set_enabled(instrument: str, body: SetEnabledRequest, session: Session = Depends(get_session)) -> dict:
    try:
        row = service.set_enabled(session, instrument, body.enabled)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"instrument": row.instrument, "enabled": row.enabled}


@router.delete("/{instrument}")
def remove_instrument(instrument: str, session: Session = Depends(get_session)) -> dict:
    service.remove_instrument(session, instrument)
    return {"ok": True}
