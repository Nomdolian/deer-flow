from dataclasses import dataclass

from app.data.schema import Candle
from app.db.models import AssetClass, Direction
from app.signals.base import Signal, SignalEngine
from app.signals.indicators import atr, candles_to_frame, ema, rsi


@dataclass(frozen=True, slots=True)
class IndicatorEngineParams:
    ema_fast: int = 20
    ema_slow: int = 50
    rsi_period: int = 14
    rsi_long_floor: float = 50.0
    rsi_long_ceiling: float = 70.0
    rsi_short_ceiling: float = 50.0
    rsi_short_floor: float = 30.0
    atr_period: int = 14
    stop_atr_mult: float = 1.5
    target_atr_mult: float = 3.0
    min_bars: int = 60


class IndicatorEngine(SignalEngine):
    """EMA crossover + RSI momentum confirmation + ATR-based stop/target sizing.
    Reimplements the EMA/RSI/ATR logic carried by the existing MQL5 EAs, natively
    in Python so it isn't locked to MetaTrader and can drive any asset class
    through the same Risk Manager (Phase 2, item 1)."""

    def __init__(self, asset_class: AssetClass, version: int = 1, params: IndicatorEngineParams | None = None):
        self.strategy_id = "indicator_ema_rsi_atr"
        self.version = version
        self.asset_class = asset_class
        self.params = params or IndicatorEngineParams()

    def evaluate(self, candles: list[Candle]) -> Signal | None:
        p = self.params
        if len(candles) < max(p.min_bars, p.ema_slow + 2, p.rsi_period + 2, p.atr_period + 2):
            return None

        df = candles_to_frame(candles)
        ema_fast = ema(df["close"], p.ema_fast)
        ema_slow = ema(df["close"], p.ema_slow)
        rsi_series = rsi(df["close"], p.rsi_period)
        atr_series = atr(df, p.atr_period)

        if ema_fast.isna().iloc[-1] or ema_slow.isna().iloc[-1] or atr_series.isna().iloc[-1]:
            return None

        prev_fast, prev_slow = ema_fast.iloc[-2], ema_slow.iloc[-2]
        cur_fast, cur_slow = ema_fast.iloc[-1], ema_slow.iloc[-1]
        last_candle = candles[-1]
        last_close = last_candle.close
        last_atr = float(atr_series.iloc[-1])
        last_rsi = float(rsi_series.iloc[-1])

        bullish_cross = prev_fast <= prev_slow and cur_fast > cur_slow
        bearish_cross = prev_fast >= prev_slow and cur_fast < cur_slow

        if not bullish_cross and not bearish_cross:
            return None
        if last_atr <= 0:
            return None  # no measurable volatility — fail closed, don't guess a stop distance

        confluences: list[str] = []
        confidence = 0.5

        if bullish_cross:
            if not (p.rsi_long_floor <= last_rsi <= p.rsi_long_ceiling):
                return None
            direction = Direction.long
            entry = last_close
            stop_loss = entry - last_atr * p.stop_atr_mult
            take_profit = entry + last_atr * p.target_atr_mult
            confluences.append(f"ema_{p.ema_fast}_{p.ema_slow}_bullish_cross")
            confluences.append(f"rsi_momentum_aligned({last_rsi:.1f})")
            confidence += 0.2
        else:
            if not (p.rsi_short_floor <= last_rsi <= p.rsi_short_ceiling):
                return None
            direction = Direction.short
            entry = last_close
            stop_loss = entry + last_atr * p.stop_atr_mult
            take_profit = entry - last_atr * p.target_atr_mult
            confluences.append(f"ema_{p.ema_fast}_{p.ema_slow}_bearish_cross")
            confluences.append(f"rsi_momentum_aligned({last_rsi:.1f})")
            confidence += 0.2

        confluences.append(f"atr_volatility({last_atr:.5f})")

        return Signal(
            instrument=last_candle.instrument,
            asset_class=self.asset_class,
            direction=direction,
            confidence_score=min(confidence, 1.0),
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confluences=confluences,
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            candle_time=last_candle.time,
            timeframe=last_candle.timeframe,
        )
