"""The veto layer. Nothing reaches execution without passing it.

Every signal is either accepted with a (possibly reduced) size, or rejected
with a named reason that is written to the journal. The reasons are the
input to the learning loop — "why didn't it trade" is as informative as
"what did it trade".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pmbot.config import RiskConfig
from pmbot.fees import edge_per_day, fee_per_share
from pmbot.risk.killswitch import KillSwitch
from pmbot.risk.sizing import cap_by_depth, kelly_shares, round_to_min_size
from pmbot.state import Context
from pmbot.strategies.base import SELL, Signal

log = logging.getLogger(__name__)

# Strategies whose size comes from market structure rather than from an
# estimated probability, and so are not sized by Kelly.
STRUCTURAL_SIZING = {"s1_maker", "s2_arb"}

# Ceiling on the journal's adaptive weight, so a good fortnight cannot keep
# raising a strategy's cap without a human deciding to raise it.
MAX_STRATEGY_WEIGHT = 1.5


@dataclass
class RiskDecision:
    accepted: bool
    reason: str = ""
    size: float = 0.0

    @classmethod
    def veto(cls, reason: str) -> RiskDecision:
        return cls(accepted=False, reason=reason)


class RiskEngine:
    def __init__(self, cfg: RiskConfig, kill_switch: KillSwitch, geoblock_passed: bool = False):
        self.cfg = cfg
        self.kill = kill_switch
        self.geoblock_passed = geoblock_passed
        self.open_order_count = 0
        self.strategy_deployed: dict[str, float] = {}
        # Adaptive allocation: the journal's nightly weights scale each
        # strategy's capital cap. Clamped so a hot streak cannot compound
        # into unbounded size, and floored by the journal so a cold one
        # never loses its sample entirely.
        self.strategy_weights: dict[str, float] = {}
        # Set by _size so the caller can report which cap actually bound.
        self._size_veto_reason = ""

    def evaluate(self, signal: Signal, ctx: Context) -> RiskDecision:
        # Cancels reduce risk and are never vetoed — including while halted.
        if signal.cancel_order_ids:
            return RiskDecision(accepted=True, reason="cancel", size=0.0)

        if self.kill.is_engaged(ctx.now):
            trip = self.kill.active_trip(ctx.now)
            return RiskDecision.veto(f"kill_switch:{trip.reason if trip else 'engaged'}")
        if self.cfg.require_geoblock_pass and not self.geoblock_passed:
            return RiskDecision.veto("geoblock_not_verified")

        book = ctx.book(signal.token_id)
        market = ctx.market(signal.token_id)
        if book is None or market is None:
            return RiskDecision.veto("unknown_token")
        if book.is_stale(self.cfg.stale_book_max_ms, ctx.now):
            return RiskDecision.veto("stale_book")
        if market.disputed:
            return RiskDecision.veto("resolution_disputed")

        if not (0.0 < signal.price < 1.0):
            return RiskDecision.veto("price_out_of_range")
        if abs(signal.price - book.round_to_tick(signal.price)) > 1e-9:
            return RiskDecision.veto("price_off_tick")

        edge_check = self._edge_veto(signal, ctx, market)
        if edge_check:
            return RiskDecision.veto(edge_check)

        size = self._size(signal, ctx, book, market)
        if size <= 0:
            return RiskDecision.veto(self._size_veto_reason or "size_zero")
        return RiskDecision(accepted=True, reason="ok", size=size)

    # ---- gates ------------------------------------------------------------

    def _edge_veto(self, signal: Signal, ctx: Context, market) -> str | None:
        fee_rate = ctx.fee_rate(signal.token_id)
        cost = 0.0 if signal.is_maker else fee_per_share(signal.price, fee_rate, ctx.fees.exponent)
        net_edge = signal.edge - cost
        floor = self.cfg.edge_floor_cents_by_strategy.get(
            signal.strategy, self.cfg.fee_adjusted_edge_min_cents
        )
        if net_edge * 100.0 < floor:
            return f"edge_below_fee_floor:{net_edge * 100:.2f}c"
        hours = market.hours_to_resolution(ctx.now)
        if hours <= 0:
            return "market_expired"
        # Capital lockup: rank on edge per day held, not raw edge.
        if edge_per_day(net_edge, hours) * 100.0 < self.cfg.min_edge_per_day_cents:
            return "edge_per_day_too_low"
        return None

    def _size(self, signal: Signal, ctx: Context, book, market) -> float:
        self._size_veto_reason = ""
        equity = ctx.equity()
        if equity <= 0:
            self._size_veto_reason = "no_equity"
            return 0.0

        if signal.side.upper() == SELL:
            # Selling closes inventory; never sell more than we hold.
            position = ctx.portfolio.positions.get(signal.token_id)
            held = position.qty if position else 0.0
            size = min(signal.size, held)
            if size <= 0:
                self._size_veto_reason = "no_inventory_to_sell"
            return round_to_min_size(size, signal.price, book.min_order_size)

        if self.open_order_count >= self.cfg.max_open_orders:
            self._size_veto_reason = "max_open_orders"
            return 0.0
        if ctx.portfolio.free_usdc < self.cfg.min_free_usdc:
            self._size_veto_reason = "min_free_usdc"
            return 0.0

        cap_pct = self.cfg.max_position_pct_per_market / 100.0
        if signal.strategy in STRUCTURAL_SIZING:
            # Making and arbitrage size off structure, not a forecast: a quote
            # is a configured clip and an arb leg is however much of the set
            # the book will sell you. Kelly on a half-cent maker edge sizes to
            # zero, which would silently disable the strategy.
            size = min(signal.size, equity * cap_pct / signal.price)
        else:
            # Kelly is the ceiling, not the target; every cap below can only reduce.
            size = min(
                signal.size,
                kelly_shares(
                    edge=max(signal.edge, 0.0),
                    price=signal.price,
                    equity=equity,
                    kelly_fraction=self.cfg.kelly_fraction,
                    cap_pct=cap_pct,
                ),
            )

        size = cap_by_depth(size, book, signal.side)
        size = self._apply_exposure_caps(signal, ctx, market, size, equity)
        size = min(size, ctx.portfolio.free_usdc / signal.price)
        sized = round_to_min_size(size, signal.price, book.min_order_size)
        if sized <= 0 and not self._size_veto_reason:
            self._size_veto_reason = "below_min_order_size"
        return sized

    def _apply_exposure_caps(self, signal: Signal, ctx: Context, market, size: float,
                             equity: float) -> float:
        marks = ctx.marks()

        position = ctx.portfolio.positions.get(signal.token_id)
        held_usd = position.value_at(marks.get(signal.token_id, signal.price)) if position else 0.0
        market_room = equity * self.cfg.max_position_pct_per_market / 100.0 - held_usd
        if market_room <= 0:
            self._size_veto_reason = "market_position_cap"
            return 0.0

        # Failure mode #8: ten markets on one election are one bet.
        event_room = float("inf")
        if market.event_id:
            exposure = ctx.event_exposure_usd(market.event_id)
            event_room = equity * self.cfg.max_exposure_pct_per_event / 100.0 - exposure
            if event_room <= 0:
                self._size_veto_reason = "event_exposure_cap"
                return 0.0

        deployed = ctx.portfolio.deployed_usd(marks)
        total_room = equity * self.cfg.max_total_deployed_pct / 100.0 - deployed
        if total_room <= 0:
            self._size_veto_reason = "total_deployed_cap"
            return 0.0

        strategy_cap_pct = self.cfg.per_strategy_cap_pct.get(signal.strategy)
        strategy_room = float("inf")
        if strategy_cap_pct is not None:
            weight = min(MAX_STRATEGY_WEIGHT, self.strategy_weights.get(signal.strategy, 1.0))
            used = self.strategy_deployed.get(signal.strategy, 0.0)
            strategy_room = equity * strategy_cap_pct * weight / 100.0 - used
            if strategy_room <= 0:
                self._size_veto_reason = "strategy_cap"
                return 0.0

        room_usd = min(market_room, event_room, total_room, strategy_room)
        return min(size, room_usd / signal.price)

    # ---- bookkeeping ------------------------------------------------------

    def note_fill(self, strategy: str, notional: float) -> None:
        self.strategy_deployed[strategy] = self.strategy_deployed.get(strategy, 0.0) + notional

    def note_close(self, strategy: str, notional: float) -> None:
        self.strategy_deployed[strategy] = max(
            0.0, self.strategy_deployed.get(strategy, 0.0) - notional
        )
