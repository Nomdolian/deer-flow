from dataclasses import dataclass, field

from app.data.schema import Candle
from app.db.models import Direction
from app.signals.base import Signal, SignalEngine


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    starting_balance: float = 10_000.0
    risk_per_trade_pct: float = 0.0025
    spread_pct: float = 0.0002
    slippage_pct: float = 0.0001
    commission_pct: float = 0.0
    warmup_bars: int = 60


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    signal: Signal
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    r_multiple: float
    opened_index: int
    closed_index: int


@dataclass
class BacktestResult:
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    ending_equity: float = 0.0


class BacktestEngine:
    """Runs a SignalEngine UNMODIFIED against historical candles: the exact same
    `evaluate(candles)` function used live, fed an expanding window so a signal
    computed "as of" candle i can only ever see candles[0:i+1] — no lookahead
    (Phase 7, items 1-2). Models spread, slippage, and commission; stop-loss is
    checked before take-profit within the same candle, matching the paper
    adapter's conservative fill assumption.

    This baseline backtester validates a single engine/instrument's edge in
    isolation (matching Build Order step 2: "prove it's profitable... for that
    one asset class" before wiring in the full portfolio Risk Manager). It does
    not model portfolio-level correlation/exposure caps across multiple
    concurrent strategies — that interaction is enforced live by RiskManager
    and is a natural next extension for multi-strategy backtesting.
    """

    def __init__(self, config: BacktestConfig | None = None):
        self.config = config or BacktestConfig()

    def run(self, engine: SignalEngine, candles: list[Candle]) -> BacktestResult:
        cfg = self.config
        equity = cfg.starting_balance
        result = BacktestResult()

        open_position: dict | None = None
        n = len(candles)

        for i in range(cfg.warmup_bars, n):
            window = candles[: i + 1]
            current = candles[i]

            if open_position is not None:
                closed = self._maybe_close(open_position, current, i, cfg)
                if closed is not None:
                    equity += closed.pnl
                    result.trades.append(closed)
                    result.equity_curve.append(equity)
                    open_position = None
                continue  # never open a second concurrent position on this instrument

            signal = engine.evaluate(window)
            if signal is None:
                continue
            if signal.candle_time != current.time:
                continue  # engine should only ever signal off the last candle in the window

            stop_distance = abs(signal.entry - signal.stop_loss)
            if stop_distance <= 0 or equity <= 0:
                continue

            risk_amount = equity * cfg.risk_per_trade_pct
            size = risk_amount / stop_distance

            spread_half = signal.entry * cfg.spread_pct / 2
            slip = signal.entry * cfg.slippage_pct
            fill_price = signal.entry + spread_half + slip if signal.direction == Direction.long else signal.entry - spread_half - slip

            open_position = {
                "signal": signal,
                "entry_price": fill_price,
                "size": size,
                "opened_index": i,
            }

        result.ending_equity = equity
        return result

    def _maybe_close(self, position: dict, candle: Candle, index: int, cfg: BacktestConfig) -> BacktestTrade | None:
        signal: Signal = position["signal"]
        hit_price: float | None = None
        if signal.direction == Direction.long:
            if candle.low <= signal.stop_loss:
                hit_price = signal.stop_loss
            elif candle.high >= signal.take_profit:
                hit_price = signal.take_profit
        else:
            if candle.high >= signal.stop_loss:
                hit_price = signal.stop_loss
            elif candle.low <= signal.take_profit:
                hit_price = signal.take_profit

        if hit_price is None:
            return None

        direction_mult = 1 if signal.direction == Direction.long else -1
        gross_pnl = (hit_price - position["entry_price"]) * direction_mult * position["size"]
        commission = position["size"] * (position["entry_price"] + hit_price) * cfg.commission_pct
        pnl = gross_pnl - commission

        risk_amount = abs(position["entry_price"] - signal.stop_loss) * position["size"]
        r_multiple = pnl / risk_amount if risk_amount else 0.0

        return BacktestTrade(
            signal=signal,
            entry_price=position["entry_price"],
            exit_price=hit_price,
            size=position["size"],
            pnl=pnl,
            r_multiple=r_multiple,
            opened_index=position["opened_index"],
            closed_index=index,
        )
