"""Local order registry: the state machine and the reconciliation loop.

PENDING -> OPEN -> PARTIAL -> FILLED | CANCELLED | REJECTED

The websocket user channel is the fast path. It is not authoritative:
messages drop, and an order the exchange thinks is live while we think it is
dead is a free option for everyone else. Reconcile against GET /orders on a
timer regardless.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class OrderState(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


TERMINAL = {OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED}


@dataclass
class TrackedOrder:
    client_id: str
    strategy: str
    token_id: str
    side: str
    price: float
    size: float
    order_type: str = "GTC"
    id: str = ""  # exchange order id, empty until acked
    filled: float = 0.0
    fee_paid: float = 0.0
    status: OrderState = OrderState.PENDING
    group_id: str | None = None
    created_ts: float = field(default_factory=time.time)
    updated_ts: float = field(default_factory=time.time)

    @property
    def remaining(self) -> float:
        return max(0.0, self.size - self.filled)

    @property
    def is_live(self) -> bool:
        return self.status not in TERMINAL


class OrderRegistry:
    def __init__(self):
        self.orders: dict[str, TrackedOrder] = {}  # keyed by client_id
        self._by_exchange_id: dict[str, str] = {}

    # ---- lifecycle --------------------------------------------------------

    def new_order(self, strategy: str, token_id: str, side: str, price: float, size: float,
                  order_type: str = "GTC", group_id: str | None = None) -> TrackedOrder:
        """Mint the client id *before* signing.

        Idempotency: a retry after a timeout reuses this id, so a request that
        actually landed can never be double-filled by the retry.
        """
        order = TrackedOrder(
            client_id=str(uuid.uuid4()), strategy=strategy, token_id=token_id, side=side,
            price=price, size=size, order_type=order_type, group_id=group_id,
        )
        self.orders[order.client_id] = order
        return order

    def ack(self, client_id: str, exchange_id: str) -> TrackedOrder | None:
        order = self.orders.get(client_id)
        if order is None:
            return None
        order.id = exchange_id
        order.status = OrderState.OPEN
        order.updated_ts = time.time()
        self._by_exchange_id[exchange_id] = client_id
        return order

    def reject(self, client_id: str, reason: str = "") -> TrackedOrder | None:
        order = self.orders.get(client_id)
        if order is None:
            return None
        order.status = OrderState.REJECTED
        order.updated_ts = time.time()
        return order

    def fill(self, key: str, price: float, size: float, fee: float = 0.0) -> TrackedOrder | None:
        order = self.get(key)
        if order is None:
            return None
        order.filled = min(order.size, order.filled + size)
        order.fee_paid += fee
        order.status = OrderState.FILLED if order.remaining <= 1e-9 else OrderState.PARTIAL
        order.updated_ts = time.time()
        return order

    def cancel(self, key: str) -> TrackedOrder | None:
        order = self.get(key)
        if order is None or not order.is_live:
            return order
        order.status = OrderState.CANCELLED
        order.updated_ts = time.time()
        return order

    # ---- reads ------------------------------------------------------------

    def get(self, key: str) -> TrackedOrder | None:
        if key in self.orders:
            return self.orders[key]
        client_id = self._by_exchange_id.get(key)
        return self.orders.get(client_id) if client_id else None

    def live_orders(self, token_id: str | None = None) -> list[TrackedOrder]:
        return [
            o for o in self.orders.values()
            if o.is_live and (token_id is None or o.token_id == token_id)
        ]

    def group(self, group_id: str) -> list[TrackedOrder]:
        return [o for o in self.orders.values() if o.group_id == group_id]

    def open_count(self) -> int:
        return len(self.live_orders())

    # ---- reconciliation ---------------------------------------------------

    def reconcile(self, exchange_orders: list[dict]) -> list[str]:
        """Align local state with the exchange's view.

        Returns human-readable descriptions of every divergence found — these
        go to the log and, if there are many, to Telegram. Divergence is
        normal; silent divergence is not.
        """
        divergences: list[str] = []
        seen: set[str] = set()

        for raw in exchange_orders:
            exchange_id = str(raw.get("id") or raw.get("orderID") or "")
            if not exchange_id:
                continue
            seen.add(exchange_id)
            order = self.get(exchange_id)
            size_matched = float(raw.get("size_matched", raw.get("sizeMatched", 0)) or 0)
            if order is None:
                # An order we have no record of: almost always a restart.
                orphan = TrackedOrder(
                    client_id=f"orphan-{exchange_id}", strategy="unknown",
                    token_id=str(raw.get("asset_id") or raw.get("market") or ""),
                    side=str(raw.get("side", "BUY")).upper(),
                    price=float(raw.get("price", 0) or 0),
                    size=float(raw.get("original_size", raw.get("size", 0)) or 0),
                    id=exchange_id, filled=size_matched, status=OrderState.OPEN,
                )
                self.orders[orphan.client_id] = orphan
                self._by_exchange_id[exchange_id] = orphan.client_id
                divergences.append(f"adopted orphan order {exchange_id}")
                continue
            if abs(order.filled - size_matched) > 1e-9:
                divergences.append(
                    f"{exchange_id}: filled {order.filled} local vs {size_matched} remote"
                )
                order.filled = size_matched
                order.status = (
                    OrderState.FILLED if order.remaining <= 1e-9
                    else OrderState.PARTIAL if size_matched > 0
                    else OrderState.OPEN
                )
                order.updated_ts = time.time()

        for order in self.live_orders():
            if order.id and order.id not in seen:
                divergences.append(f"{order.id}: live locally, absent remotely -> cancelled")
                order.status = OrderState.CANCELLED
                order.updated_ts = time.time()
        return divergences

    def mark_all_cancelled(self, reason: str = "exchange_cancel_all") -> int:
        """V2 cutovers and maintenance windows cancel every resting order.
        Treat it as a normal state, not an exception.
        """
        count = 0
        for order in self.live_orders():
            order.status = OrderState.CANCELLED
            order.updated_ts = time.time()
            count += 1
        return count
