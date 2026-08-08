from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.data.schema import Candle
from app.db.base import Base
from app.db.models import AssetClass


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine)
    session = session_local()
    try:
        yield session
    finally:
        session.close()


def make_candles(
    n: int,
    *,
    instrument: str = "EURUSD",
    asset_class: AssetClass = AssetClass.forex,
    timeframe: str = "H1",
    start_price: float = 1.1000,
    step: float = 0.0002,
    trend: float = 0.0,
) -> list[Candle]:
    """Simple synthetic candle generator for deterministic engine tests."""
    candles = []
    price = start_price
    start_time = datetime(2024, 1, 1, tzinfo=UTC)
    for i in range(n):
        open_p = price
        close_p = price + trend + (step if i % 2 == 0 else -step / 2)
        high_p = max(open_p, close_p) + step / 4
        low_p = min(open_p, close_p) - step / 4
        candles.append(
            Candle(
                instrument=instrument,
                asset_class=asset_class,
                timeframe=timeframe,
                time=start_time + timedelta(hours=i),
                open=open_p,
                high=high_p,
                low=low_p,
                close=close_p,
                volume=100.0 + i,
            )
        )
        price = close_p
    return candles
