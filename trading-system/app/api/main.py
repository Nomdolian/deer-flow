from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    devices,
    health,
    journal,
    killswitch,
    positions,
    signals,
    strategies,
    watchlist,
)
from app.config import settings

app = FastAPI(
    title="Trading System API",
    description="Read/control surface for PC and mobile clients. Never a trading-logic host — "
    "see Phase 8 in the spec: mobile and desktop are clients of the always-on server, not execution hosts.",
)

# Only the web build of the mobile app needs CORS — a native iOS/Android app
# isn't subject to it. Off unless API_CORS_ORIGINS names exact origins, and a
# wildcard is refused rather than honoured: the API can halt or resume live
# trading, so "any website may call this" is never a sane default.
_origins = [o.strip() for o in settings.api_cors_origins.split(",") if o.strip() and o.strip() != "*"]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["x-api-key", "x-totp-code", "content-type"],
    )

app.include_router(health.router)
app.include_router(positions.router)
app.include_router(signals.router)
app.include_router(journal.router)
app.include_router(strategies.router)
app.include_router(killswitch.router)
app.include_router(devices.router)
app.include_router(watchlist.router)
