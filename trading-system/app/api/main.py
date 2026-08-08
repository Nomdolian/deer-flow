from fastapi import FastAPI

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

app = FastAPI(
    title="Trading System API",
    description="Read/control surface for PC and mobile clients. Never a trading-logic host — "
    "see Phase 8 in the spec: mobile and desktop are clients of the always-on server, not execution hosts.",
)

app.include_router(health.router)
app.include_router(positions.router)
app.include_router(signals.router)
app.include_router(journal.router)
app.include_router(strategies.router)
app.include_router(killswitch.router)
app.include_router(devices.router)
app.include_router(watchlist.router)
