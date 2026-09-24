"""Backtest / paper metrics, and the gate a strategy must clear to go live."""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise

MIN_TRADES_FOR_LIVE = 100
MIN_SHARPE_FOR_LIVE = 1.0


@dataclass
class Metrics:
    trades: int
    wins: int
    pnl: float
    expectancy: float
    win_rate: float
    max_drawdown_pct: float
    sharpe: float | None
    fees_paid: float = 0.0
    rebates_earned: float = 0.0


def max_drawdown_pct(equity_curve: list[float]) -> float:
    peak = float("-inf")
    worst = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak * 100.0)
    return worst


def sharpe(equity_curve: list[float], periods_per_year: float = 365.0) -> float | None:
    if len(equity_curve) < 3:
        return None
    returns = [(b - a) / a for a, b in pairwise(equity_curve) if a > 0]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    if variance <= 0:
        return None
    return (mean / math.sqrt(variance)) * math.sqrt(periods_per_year)


def compute(pnls: list[float], equity_curve: list[float], fees_paid: float = 0.0,
            rebates_earned: float = 0.0) -> Metrics:
    trades = len(pnls)
    wins = len([p for p in pnls if p > 0])
    total = sum(pnls)
    return Metrics(
        trades=trades,
        wins=wins,
        pnl=total,
        expectancy=total / trades if trades else 0.0,
        win_rate=wins / trades if trades else 0.0,
        max_drawdown_pct=max_drawdown_pct(equity_curve),
        sharpe=sharpe(equity_curve),
        fees_paid=fees_paid,
        rebates_earned=rebates_earned,
    )


def gate_for_live(metrics: Metrics, daily_loss_kill_pct: float) -> tuple[bool, list[str]]:
    """Do not compress phases 4 and 5.

    Returns (pass, failures). Every failure is a reason the strategy has not
    yet been observed against real order-book dynamics for long enough.
    """
    failures: list[str] = []
    if metrics.trades < MIN_TRADES_FOR_LIVE:
        failures.append(f"only {metrics.trades} trades (need {MIN_TRADES_FOR_LIVE})")
    if metrics.expectancy <= 0:
        failures.append(f"expectancy {metrics.expectancy:.4f} is not positive after fees")
    if metrics.max_drawdown_pct >= daily_loss_kill_pct:
        failures.append(
            f"max drawdown {metrics.max_drawdown_pct:.2f}% exceeds the daily kill threshold "
            f"({daily_loss_kill_pct}%)"
        )
    if metrics.sharpe is None or metrics.sharpe <= MIN_SHARPE_FOR_LIVE:
        failures.append(f"sharpe {metrics.sharpe} is not above {MIN_SHARPE_FOR_LIVE}")
    return (not failures), failures
