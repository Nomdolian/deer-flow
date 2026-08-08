from dataclasses import dataclass

from app.data.schema import Candle
from app.db.models import AssetClass, Direction
from app.signals.base import Signal, SignalEngine
from app.signals.indicators import candles_to_frame


@dataclass(frozen=True, slots=True)
class MemeMomentumParams:
    lookback: int = 20
    price_move_threshold_pct: float = 0.08  # 8% move over lookback to count as momentum
    volume_spike_mult: float = 3.0  # current volume vs rolling average
    min_bars: int = 30


class MemeMomentumEngine(SignalEngine):
    """Deliberately simple momentum/volume-spike engine for meme coins.

    SMC structure analysis assumes reasonably orderly, liquid price action;
    meme coins are low-liquidity and high-manipulation, so they get their own
    engine rather than being scored/sized by the SMC or indicator engines used
    for majors (Phase 2, item 4). This engine only ever feeds the Risk Manager's
    isolated meme-coin bucket — it is never compared against major-asset signals
    on the same scale.
    """

    def __init__(self, version: int = 1, params: MemeMomentumParams | None = None):
        self.strategy_id = "meme_momentum_volume"
        self.version = version
        self.asset_class = AssetClass.crypto_meme
        self.params = params or MemeMomentumParams()

    def evaluate(self, candles: list[Candle]) -> Signal | None:
        p = self.params
        if len(candles) < p.min_bars:
            return None

        df = candles_to_frame(candles)
        window = df.tail(p.lookback)
        start_price = window["close"].iloc[0]
        last_close = window["close"].iloc[-1]
        if start_price <= 0:
            return None

        move_pct = (last_close - start_price) / start_price
        avg_volume = df["volume"].tail(p.lookback * 3).mean()
        last_volume = df["volume"].iloc[-1]
        if avg_volume <= 0:
            return None
        volume_ratio = last_volume / avg_volume

        confluences: list[str] = []
        confidence = 0.3

        if abs(move_pct) < p.price_move_threshold_pct:
            return None
        confluences.append(f"momentum_move({move_pct:+.1%})")
        confidence += 0.2

        if volume_ratio < p.volume_spike_mult:
            return None  # momentum without a real volume spike is easy to fake on thin books
        confluences.append(f"volume_spike(x{volume_ratio:.1f})")
        confidence += 0.3

        direction = Direction.long if move_pct > 0 else Direction.short
        entry = last_close
        recent_range = window["high"].max() - window["low"].min()
        stop_distance = max(recent_range * 0.5, entry * 0.05)  # meme coins need wide stops; never near-zero
        if direction == Direction.long:
            stop_loss = entry - stop_distance
            take_profit = entry + stop_distance * 1.5
        else:
            stop_loss = entry + stop_distance
            take_profit = entry - stop_distance * 1.5

        return Signal(
            instrument=candles[-1].instrument,
            asset_class=AssetClass.crypto_meme,
            direction=direction,
            confidence_score=min(confidence, 1.0),
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confluences=confluences,
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            candle_time=candles[-1].time,
            timeframe=candles[-1].timeframe,
        )
