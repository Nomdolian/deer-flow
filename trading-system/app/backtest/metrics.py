from dataclasses import dataclass

from app.backtest.engine import BacktestResult


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    sample_size: int
    win_rate: float
    expectancy_r: float
    avg_r: float
    max_drawdown_pct: float
    ending_equity: float
    total_return_pct: float


def compute_metrics(result: BacktestResult, starting_balance: float) -> BacktestMetrics:
    trades = result.trades
    if not trades:
        return BacktestMetrics(0, 0.0, 0.0, 0.0, 0.0, starting_balance, 0.0)

    r_values = [t.r_multiple for t in trades]
    wins = [r for r in r_values if r > 0]
    win_rate = len(wins) / len(r_values)
    expectancy_r = sum(r_values) / len(r_values)

    equity_curve = [starting_balance, *result.equity_curve]
    running_max = equity_curve[0]
    max_drawdown_pct = 0.0
    for equity in equity_curve:
        running_max = max(running_max, equity)
        if running_max > 0:
            drawdown_pct = (running_max - equity) / running_max
            max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)

    total_return_pct = (result.ending_equity - starting_balance) / starting_balance if starting_balance else 0.0

    return BacktestMetrics(
        sample_size=len(trades),
        win_rate=win_rate,
        expectancy_r=expectancy_r,
        avg_r=expectancy_r,
        max_drawdown_pct=max_drawdown_pct,
        ending_equity=result.ending_equity,
        total_return_pct=total_return_pct,
    )
