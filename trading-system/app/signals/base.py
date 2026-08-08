from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from app.data.schema import Candle
from app.db.models import AssetClass, Direction


@dataclass(frozen=True, slots=True)
class Signal:
    """The structured output every signal engine produces. No engine places
    orders directly — signals go to the Risk Manager, and only the Risk Manager
    decides whether/how large a resulting order is."""

    instrument: str
    asset_class: AssetClass
    direction: Direction
    confidence_score: float  # 0.0-1.0
    entry: float
    stop_loss: float
    take_profit: float
    confluences: list[str]
    strategy_id: str
    strategy_version: int
    candle_time: datetime
    timeframe: str = field(default="")

    @property
    def risk_reward(self) -> float:
        risk = abs(self.entry - self.stop_loss)
        reward = abs(self.take_profit - self.entry)
        return reward / risk if risk else 0.0


class SignalEngine(ABC):
    """Pure, testable, deterministic: candles-in, signal-out (or None). No network
    calls, no LLM calls, no side effects — this is what makes it backtestable with
    the exact same function used live (Phase 7, item 1)."""

    strategy_id: str
    version: int
    asset_class: AssetClass

    @abstractmethod
    def evaluate(self, candles: list[Candle]) -> Signal | None:
        """candles must be closed candles, oldest-first, most-recent last. The
        engine must only ever look at data available at candles[-1]'s close —
        no lookahead."""
