from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth import require_api_key
from app.db.base import get_session
from app.db.models import StrategyVersion
from app.journal.stats import compute_stats

router = APIRouter(prefix="/strategies", tags=["strategies"], dependencies=[Depends(require_api_key)])


@router.get("")
def list_strategies(session: Session = Depends(get_session)) -> list[dict]:
    versions = session.query(StrategyVersion).order_by(StrategyVersion.strategy_id, StrategyVersion.version).all()
    return [
        {
            "strategy_id": v.strategy_id,
            "version": v.version,
            "asset_class": v.asset_class.value,
            "is_paused": v.is_paused,
            "size_multiplier": v.size_multiplier,
            "consecutive_losses": v.consecutive_losses,
            "backtest_expectancy_r": v.backtest_expectancy_r,
            "backtest_win_rate": v.backtest_win_rate,
        }
        for v in versions
    ]


@router.get("/{strategy_id}/{version}/performance")
def strategy_performance(strategy_id: str, version: int, instrument: str, session: Session = Depends(get_session)) -> dict:
    stats = compute_stats(session, strategy_id, version, instrument)
    if stats is None:
        return {"sample_size": 0}
    return {
        "sample_size": stats.sample_size,
        "win_rate": stats.win_rate,
        "expectancy_r": stats.expectancy_r,
        "avg_r": stats.avg_r,
        "max_drawdown_r": stats.max_drawdown_r,
    }
