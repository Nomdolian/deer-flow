"""SMC/ICT structure engine: swing detection, BOS/CHoCH, order blocks, fair
value gaps, liquidity sweeps, and premium/discount zoning — computed in code,
not eyeballed by an LLM. This is a deterministic baseline implementation of the
concepts named in the spec (Phase 2, item 1); tune thresholds against your
existing MQL5 SMC logic before relying on it for sizing decisions.
"""

from dataclasses import dataclass
from enum import Enum

from app.data.schema import Candle
from app.db.models import AssetClass, Direction
from app.signals.base import Signal, SignalEngine


class SwingKind(str, Enum):
    high = "high"
    low = "low"


@dataclass(frozen=True, slots=True)
class SwingPoint:
    index: int
    kind: SwingKind
    price: float


class StructureEvent(str, Enum):
    bos_bullish = "bos_bullish"
    bos_bearish = "bos_bearish"
    choch_bullish = "choch_bullish"
    choch_bearish = "choch_bearish"


@dataclass(frozen=True, slots=True)
class FVG:
    index: int  # index of the middle (impulse) candle
    direction: Direction
    top: float
    bottom: float


def detect_swings(candles: list[Candle], left: int = 2, right: int = 2) -> list[SwingPoint]:
    """Fractal swing points: a candle is a swing high if its high is the max of
    the `left`+`right` candles around it (strictly greater than each neighbor
    used only as a tie-break via index order), likewise for swing lows."""
    swings: list[SwingPoint] = []
    n = len(candles)
    for i in range(left, n - right):
        window = candles[i - left : i + right + 1]
        highs = [c.high for c in window]
        lows = [c.low for c in window]
        if candles[i].high == max(highs) and highs.count(candles[i].high) == 1:
            swings.append(SwingPoint(index=i, kind=SwingKind.high, price=candles[i].high))
        if candles[i].low == min(lows) and lows.count(candles[i].low) == 1:
            swings.append(SwingPoint(index=i, kind=SwingKind.low, price=candles[i].low))
    return swings


def last_structure_event(candles: list[Candle], swings: list[SwingPoint]) -> tuple[StructureEvent, SwingPoint] | None:
    """Walk swings in order, tracking trend state (bullish/bearish/unknown).
    A break of the most recent opposite-type swing in the direction of the
    established trend is a BOS (continuation); a break against the established
    trend is a CHoCH (reversal). Returns the most recent such event relative to
    the last closed candle, if any.
    """
    highs = [s for s in swings if s.kind == SwingKind.high]
    lows = [s for s in swings if s.kind == SwingKind.low]
    if not highs or not lows:
        return None

    trend: str | None = None  # "bullish" | "bearish"
    last_event: tuple[StructureEvent, SwingPoint] | None = None
    last_broken_high: SwingPoint | None = None
    last_broken_low: SwingPoint | None = None

    n = len(candles)
    for i in range(n):
        close = candles[i].close
        candidate_highs = [s for s in highs if s.index < i and (last_broken_high is None or s.index > last_broken_high.index)]
        candidate_lows = [s for s in lows if s.index < i and (last_broken_low is None or s.index > last_broken_low.index)]

        if candidate_highs:
            nearest_high = min(candidate_highs, key=lambda s: s.index)
            if close > nearest_high.price:
                event = StructureEvent.bos_bullish if trend in (None, "bullish") else StructureEvent.choch_bullish
                trend = "bullish"
                last_broken_high = nearest_high
                last_event = (event, nearest_high)

        if candidate_lows:
            nearest_low = min(candidate_lows, key=lambda s: s.index)
            if close < nearest_low.price:
                event = StructureEvent.bos_bearish if trend in (None, "bearish") else StructureEvent.choch_bearish
                trend = "bearish"
                last_broken_low = nearest_low
                last_event = (event, nearest_low)

    return last_event


def detect_order_block(candles: list[Candle], break_index: int, direction: Direction) -> tuple[float, float] | None:
    """The order block for a bullish break is the last bearish (down) candle
    before the impulsive move that broke structure; for a bearish break, the
    last bullish (up) candle. Returns (low, high) of that candle's body+wick
    range, or None if not found within a short lookback."""
    lookback = candles[max(0, break_index - 10) : break_index]
    for candle in reversed(lookback):
        is_down = candle.close < candle.open
        is_up = candle.close > candle.open
        if direction == Direction.long and is_down:
            return candle.low, candle.high
        if direction == Direction.short and is_up:
            return candle.low, candle.high
    return None


def detect_fair_value_gaps(candles: list[Candle], start: int = 0) -> list[FVG]:
    """3-candle imbalance: gap between candle[i-1] and candle[i+1] left by the
    impulsive middle candle i, with no overlap in between."""
    gaps: list[FVG] = []
    for i in range(max(1, start), len(candles) - 1):
        prev_c, next_c = candles[i - 1], candles[i + 1]
        if next_c.low > prev_c.high:
            gaps.append(FVG(index=i, direction=Direction.long, top=next_c.low, bottom=prev_c.high))
        elif next_c.high < prev_c.low:
            gaps.append(FVG(index=i, direction=Direction.short, top=prev_c.low, bottom=next_c.high))
    return gaps


def detect_liquidity_sweep(candles: list[Candle], swings: list[SwingPoint], lookahead_from: int) -> tuple[SwingPoint, int] | None:
    """A liquidity sweep: price wicks beyond a prior swing high/low (grabbing
    resting stop-loss liquidity) and then closes back on the origin side of it,
    within a short window after that swing formed. Returns the swept swing and
    the index of the candle that performed the sweep."""
    for swing in sorted(swings, key=lambda s: -s.index):
        if swing.index >= lookahead_from:
            continue
        for i in range(swing.index + 1, min(len(candles), swing.index + 15)):
            c = candles[i]
            if swing.kind == SwingKind.low and c.low < swing.price and c.close > swing.price:
                return swing, i
            if swing.kind == SwingKind.high and c.high > swing.price and c.close < swing.price:
                return swing, i
        break  # only check the most recent relevant swing
    return None


def premium_discount_zone(dealing_high: float, dealing_low: float, price: float) -> str:
    if dealing_high == dealing_low:
        return "equilibrium"
    equilibrium = (dealing_high + dealing_low) / 2
    if price > equilibrium:
        return "premium"
    if price < equilibrium:
        return "discount"
    return "equilibrium"


@dataclass(frozen=True, slots=True)
class SMCEngineParams:
    swing_left: int = 2
    swing_right: int = 2
    min_bars: int = 40
    min_risk_reward: float = 1.5
    target_atr_mult: float = 0.0  # unused; target derived from dealing range instead


class SMCICTEngine(SignalEngine):
    """BOS/CHoCH + liquidity sweep + order block/FVG confluence + premium-discount
    filtering, all computed deterministically from closed candles."""

    def __init__(self, asset_class: AssetClass, version: int = 1, params: SMCEngineParams | None = None):
        self.strategy_id = "smc_ict_structure"
        self.version = version
        self.asset_class = asset_class
        self.params = params or SMCEngineParams()

    def evaluate(self, candles: list[Candle]) -> Signal | None:
        p = self.params
        if len(candles) < p.min_bars:
            return None

        swings = detect_swings(candles, left=p.swing_left, right=p.swing_right)
        if len(swings) < 2:
            return None

        event = last_structure_event(candles, swings)
        if event is None:
            return None
        struct_event, _broken_swing = event

        last_index = len(candles) - 1
        bullish_events = (StructureEvent.bos_bullish, StructureEvent.choch_bullish)
        direction = Direction.long if struct_event in bullish_events else Direction.short

        sweep = detect_liquidity_sweep(candles, swings, lookahead_from=last_index)
        confluences: list[str] = [struct_event.value]
        confidence = 0.4

        if sweep is not None:
            swept_swing, sweep_index = sweep
            expected_kind = SwingKind.low if direction == Direction.long else SwingKind.high
            if swept_swing.kind == expected_kind and sweep_index >= last_index - 15:
                confluences.append(f"liquidity_sweep({swept_swing.kind.value}@{swept_swing.price:.5f})")
                confidence += 0.2

        order_block = detect_order_block(candles, last_index, direction)
        if order_block is not None:
            ob_low, ob_high = order_block
            confluences.append(f"order_block({ob_low:.5f}-{ob_high:.5f})")
            confidence += 0.15

        fvgs = detect_fair_value_gaps(candles, start=max(0, last_index - 20))
        aligned_fvgs = [g for g in fvgs if g.direction == direction]
        if aligned_fvgs:
            confluences.append(f"fvg({aligned_fvgs[-1].bottom:.5f}-{aligned_fvgs[-1].top:.5f})")
            confidence += 0.15

        window = candles[max(0, last_index - 30) : last_index + 1]
        dealing_high = max(c.high for c in window)
        dealing_low = min(c.low for c in window)
        last_close = candles[last_index].close
        zone = premium_discount_zone(dealing_high, dealing_low, last_close)
        wants_zone = "discount" if direction == Direction.long else "premium"
        if zone != wants_zone:
            return None  # only take entries on the "cheap" side of the dealing range
        confluences.append(f"{zone}_zone")
        confidence += 0.1

        if len(confluences) < 3:
            return None  # structure break alone isn't enough — require real confluence

        entry = last_close
        if direction == Direction.long:
            stop_loss = min(dealing_low, order_block[0] if order_block else dealing_low) * 0.999
            take_profit = dealing_high
        else:
            stop_loss = max(dealing_high, order_block[1] if order_block else dealing_high) * 1.001
            take_profit = dealing_low

        risk = abs(entry - stop_loss)
        reward = abs(take_profit - entry)
        if risk <= 0 or reward / risk < p.min_risk_reward:
            return None

        return Signal(
            instrument=candles[last_index].instrument,
            asset_class=self.asset_class,
            direction=direction,
            confidence_score=min(confidence, 1.0),
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confluences=confluences,
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            candle_time=candles[last_index].time,
            timeframe=candles[last_index].timeframe,
        )
