"""Execution engine: sign, post, track, cancel.

The mode switch (paper | live) is the *only* difference between simulated
and real trading — same signals, same risk gate, same registry, same
journal. That is what makes 30 days of paper trading meaningful.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from pmbot.data.book import OrderBook
from pmbot.execution.registry import OrderRegistry, TrackedOrder
from pmbot.state import Context
from pmbot.strategies.base import BUY, SELL, Signal

log = logging.getLogger(__name__)


class Executor(Protocol):
    async def post(self, order: TrackedOrder, book: OrderBook | None = None) -> str | None: ...
    async def cancel(self, order_ids: list[str]) -> int: ...
    async def cancel_all(self) -> int: ...


@dataclass
class Fill:
    client_id: str
    strategy: str
    token_id: str
    side: str
    price: float
    size: float
    fee: float
    is_maker: bool
    ts: float


FillHook = Callable[[Fill], Awaitable[None]]


class ExecutionEngine:
    def __init__(
        self,
        executor: Executor,
        registry: OrderRegistry,
        ctx: Context,
        on_fill: FillHook | None = None,
        unwind_timeout_s: float = 2.0,
    ):
        self.executor = executor
        self.registry = registry
        self.ctx = ctx
        self.on_fill = on_fill
        self.unwind_timeout_s = unwind_timeout_s

    # ---- submission -------------------------------------------------------

    async def submit(self, signal: Signal, size: float) -> TrackedOrder | None:
        if signal.cancel_order_ids:
            await self.executor.cancel(signal.cancel_order_ids)
            return None
        order = self.registry.new_order(
            strategy=signal.strategy,
            token_id=signal.token_id,
            side=signal.side,
            price=signal.price,
            size=size,
            order_type=signal.order_type,
            group_id=signal.group_id,
        )
        await self.executor.post(order, self.ctx.book(signal.token_id))
        return order

    async def submit_group(self, signals: list[Signal], sizes: list[float]) -> list[TrackedOrder]:
        """Arbitrage legs: post together, then unwind anything left naked.

        There is no atomic multi-leg primitive — a batch is the closest the
        API offers. A set that only half-fills is a directional position we
        never wanted, so it is unwound immediately rather than held.
        """
        orders = [
            self.registry.new_order(
                strategy=s.strategy, token_id=s.token_id, side=s.side, price=s.price,
                size=size, order_type=s.order_type, group_id=s.group_id,
            )
            for s, size in zip(signals, sizes)
        ]
        post_batch = getattr(self.executor, "post_batch", None)
        if post_batch is not None:
            await post_batch(orders)
        else:
            await asyncio.gather(
                *(self.executor.post(o, self.ctx.book(o.token_id)) for o in orders)
            )

        await asyncio.sleep(self.unwind_timeout_s)
        group_id = signals[0].group_id if signals else None
        if group_id:
            await self.unwind_if_partial(group_id)
        return orders

    async def unwind_if_partial(self, group_id: str) -> bool:
        """If any leg of a set missed, flatten the legs that landed."""
        legs = self.registry.group(group_id)
        if not legs:
            return False
        complete = all(leg.remaining <= 1e-9 for leg in legs)
        if complete:
            return False

        log.warning("unwinding partial arb group %s", group_id)
        await self.executor.cancel([leg.client_id for leg in legs if leg.is_live])
        for leg in legs:
            if leg.filled <= 0:
                continue
            book = self.ctx.book(leg.token_id)
            if book is None or book.best_bid is None:
                log.error("cannot unwind %s: no book", leg.token_id)
                continue
            unwind = self.registry.new_order(
                strategy=leg.strategy, token_id=leg.token_id,
                side=SELL if leg.side.upper() == BUY else BUY,
                price=book.round_to_tick(book.best_bid),
                size=leg.filled, order_type="FAK", group_id=f"{group_id}:unwind",
            )
            await self.executor.post(unwind, book)
        return True

    # ---- fills ------------------------------------------------------------

    async def handle_fill(self, client_id: str, price: float, size: float, fee: float,
                          is_maker: bool, ts: float) -> Fill | None:
        order = self.registry.get(client_id)
        if order is None:
            log.warning("fill for unknown order %s", client_id)
            return None
        position = self.ctx.portfolio.position(order.token_id)
        realized = position.apply_fill(order.side, price, size, fee)
        if order.side.upper() == BUY:
            self.ctx.portfolio.free_usdc -= price * size + fee
        else:
            self.ctx.portfolio.free_usdc += price * size - fee
        self.ctx.portfolio.daily_realized_pnl += realized

        fill = Fill(
            client_id=client_id, strategy=order.strategy, token_id=order.token_id,
            side=order.side, price=price, size=size, fee=fee, is_maker=is_maker, ts=ts,
        )
        if self.on_fill is not None:
            await self.on_fill(fill)
        return fill

    # ---- shutdown ---------------------------------------------------------

    async def shutdown(self) -> int:
        """An orphaned resting order is a free option for everyone else."""
        cancelled = await self.executor.cancel_all()
        log.info("shutdown: cancelled %d resting orders", cancelled)
        return cancelled
