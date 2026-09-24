"""Per-strategy journal and adaptive weighting.

Recompute nightly: a decayed win-rate x expectancy score, normalised, with a
floor. The floor matters — zeroing a strategy out stops the sample, and a
strategy with no sample can never earn its way back.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from itertools import pairwise

HALF_LIFE_DAYS = 14.0
MIN_WEIGHT = 0.1
MIN_TRADES_FOR_CONFIDENCE = 20


@dataclass
class TradeResult:
    strategy: str
    pnl: float
    ts: float
    edge_predicted: float = 0.0
    edge_realised: float = 0.0


@dataclass
class StrategyStats:
    strategy: str
    trades: int = 0
    wins: int = 0
    pnl: float = 0.0
    weight: float = 1.0
    decayed_score: float = 0.0
    edge_predicted: float = 0.0
    edge_realised: float = 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0

    @property
    def expectancy(self) -> float:
        return self.pnl / self.trades if self.trades else 0.0

    @property
    def edge_honesty(self) -> float | None:
        """Realised edge over predicted edge.

        Below 1 means the model is optimistic — the single most useful number
        in the weekly digest, because it says whether to believe the model at
        all.
        """
        if self.edge_predicted <= 0:
            return None
        return self.edge_realised / self.edge_predicted


def decay_factor(age_days: float, half_life_days: float = HALF_LIFE_DAYS) -> float:
    return 0.5 ** (age_days / half_life_days)


class Journal:
    def __init__(self, half_life_days: float = HALF_LIFE_DAYS, min_weight: float = MIN_WEIGHT):
        self.half_life_days = half_life_days
        self.min_weight = min_weight
        self.results: list[TradeResult] = []
        self.stats: dict[str, StrategyStats] = {}

    def record(self, result: TradeResult) -> None:
        self.results.append(result)
        stats = self.stats.setdefault(result.strategy, StrategyStats(strategy=result.strategy))
        stats.trades += 1
        stats.wins += 1 if result.pnl > 0 else 0
        stats.pnl += result.pnl
        stats.edge_predicted += result.edge_predicted
        stats.edge_realised += result.edge_realised

    def consecutive_losses(self, strategy: str | None = None) -> int:
        count = 0
        for result in reversed(self.results):
            if strategy and result.strategy != strategy:
                continue
            if result.pnl < 0:
                count += 1
            else:
                break
        return count

    def recompute_weights(self, now: float | None = None) -> dict[str, float]:
        """Score = decayed expectancy, shrunk toward zero on a thin sample.

        A strategy with six trades and a great record has told you nothing
        yet; the shrink is what stops the bot from chasing noise.
        """
        now = now if now is not None else time.time()
        scores: dict[str, float] = {}
        for strategy, stats in self.stats.items():
            weighted_pnl = 0.0
            weight_sum = 0.0
            wins = 0.0
            for result in self.results:
                if result.strategy != strategy:
                    continue
                age_days = max(0.0, (now - result.ts) / 86400.0)
                factor = decay_factor(age_days, self.half_life_days)
                weighted_pnl += result.pnl * factor
                weight_sum += factor
                wins += factor if result.pnl > 0 else 0.0
            if weight_sum <= 0:
                scores[strategy] = 0.0
                continue
            expectancy = weighted_pnl / weight_sum
            win_rate = wins / weight_sum
            confidence = min(1.0, stats.trades / MIN_TRADES_FOR_CONFIDENCE)
            scores[strategy] = max(0.0, expectancy) * win_rate * confidence
            stats.decayed_score = scores[strategy]

        total = sum(scores.values())
        weights: dict[str, float] = {}
        for strategy in self.stats:
            raw = scores.get(strategy, 0.0)
            share = raw / total if total > 0 else 1.0 / max(1, len(self.stats))
            weight = max(self.min_weight, share * len(self.stats))
            weights[strategy] = round(weight, 4)
            self.stats[strategy].weight = weights[strategy]
        return weights

    def sharpe(self, equity_curve: list[tuple[int, float]]) -> float | None:
        """Daily Sharpe on the equity curve. One of the gates for going live."""
        if len(equity_curve) < 3:
            return None
        returns = []
        for (_, previous), (_, current) in pairwise(equity_curve):
            if previous > 0:
                returns.append((current - previous) / previous)
        if len(returns) < 2:
            return None
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        if variance <= 0:
            return None
        return (mean / math.sqrt(variance)) * math.sqrt(365)

    def summary(self) -> list[StrategyStats]:
        return sorted(self.stats.values(), key=lambda s: -s.pnl)
