"""Live execution against the CLOB V2 API.

The SDK is synchronous, so every call is pushed to a worker thread — one
blocking HTTP call inside the event loop stalls the websocket feeds and the
book goes stale, which is exactly the state the risk engine halts on.
"""

from __future__ import annotations

import asyncio
import logging

from pmbot.data.book import OrderBook
from pmbot.execution.registry import OrderRegistry, TrackedOrder

log = logging.getLogger(__name__)


class LiveExecutor:
    def __init__(self, client, registry: OrderRegistry, on_api_error=None):
        self.client = client
        self.registry = registry
        self.on_api_error = on_api_error

    # ---- orders -----------------------------------------------------------

    async def post(self, order: TrackedOrder, book: OrderBook | None = None) -> str | None:
        from py_clob_client_v2.clob_types import OrderArgsV2

        args = OrderArgsV2(
            token_id=order.token_id,
            price=order.price,
            size=order.size,
            side=order.side.upper(),
        )
        try:
            # post_only on GTC: the exchange rejects rather than crossing, so a
            # maker strategy can never accidentally pay taker fees.
            response = await asyncio.to_thread(
                self.client.create_and_post_order,
                args,
                None,
                order.order_type,
                order.order_type == "GTC",
            )
        except Exception as exc:  # noqa: BLE001 - SDK raises a family of errors
            log.warning("post failed for %s: %s", order.client_id, exc)
            self.registry.reject(order.client_id, str(exc))
            if callable(self.on_api_error):
                self.on_api_error(exc)
            return None

        exchange_id = str(
            (response or {}).get("orderID") or (response or {}).get("orderId") or ""
        )
        if not exchange_id or (response or {}).get("success") is False:
            self.registry.reject(order.client_id, str(response))
            return None
        self.registry.ack(order.client_id, exchange_id)
        return exchange_id

    async def post_batch(self, orders: list[TrackedOrder]) -> list[str | None]:
        """Batch submit, capped at the exchange's 15-order limit.

        Used for arbitrage legs, where the whole point is getting the set on
        in one round trip.
        """
        from py_clob_client_v2.clob_types import OrderArgsV2, PostOrdersV2Args

        results: list[str | None] = []
        for start in range(0, len(orders), 15):
            chunk = orders[start : start + 15]
            args = [
                PostOrdersV2Args(
                    order=self.client.create_order(
                        OrderArgsV2(
                            token_id=o.token_id, price=o.price, size=o.size, side=o.side.upper()
                        )
                    ),
                    orderType=o.order_type,
                )
                for o in chunk
            ]
            try:
                responses = await asyncio.to_thread(self.client.post_orders, args)
            except Exception as exc:  # noqa: BLE001
                log.warning("batch post failed: %s", exc)
                for order in chunk:
                    self.registry.reject(order.client_id, str(exc))
                if callable(self.on_api_error):
                    self.on_api_error(exc)
                results.extend([None] * len(chunk))
                continue
            for order, response in zip(chunk, responses or []):
                exchange_id = str((response or {}).get("orderID") or "")
                if exchange_id:
                    self.registry.ack(order.client_id, exchange_id)
                    results.append(exchange_id)
                else:
                    self.registry.reject(order.client_id, str(response))
                    results.append(None)
        return results

    async def cancel(self, order_ids: list[str]) -> int:
        exchange_ids = []
        for order_id in order_ids:
            order = self.registry.get(order_id)
            if order and order.id:
                exchange_ids.append(order.id)
        if not exchange_ids:
            return 0
        try:
            await asyncio.to_thread(self.client.cancel_orders, exchange_ids)
        except Exception as exc:  # noqa: BLE001
            log.warning("cancel failed: %s", exc)
            if callable(self.on_api_error):
                self.on_api_error(exc)
            return 0
        for order_id in order_ids:
            self.registry.cancel(order_id)
        return len(exchange_ids)

    async def cancel_all(self) -> int:
        try:
            await asyncio.to_thread(self.client.cancel_all)
        except Exception as exc:  # noqa: BLE001
            log.error("cancel_all failed: %s", exc)
            if callable(self.on_api_error):
                self.on_api_error(exc)
            return 0
        return self.registry.mark_all_cancelled("cancel_all")

    # ---- reconciliation ---------------------------------------------------

    async def fetch_open_orders(self) -> list[dict]:
        try:
            return await asyncio.to_thread(self.client.get_open_orders) or []
        except Exception as exc:  # noqa: BLE001
            log.warning("get_open_orders failed: %s", exc)
            if callable(self.on_api_error):
                self.on_api_error(exc)
            return []

    async def fetch_balance(self) -> float | None:
        from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

        try:
            raw = await asyncio.to_thread(
                self.client.get_balance_allowance,
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("balance fetch failed: %s", exc)
            return None
        balance = (raw or {}).get("balance")
        # USDC is 6-decimal fixed point on chain.
        return float(balance) / 1e6 if balance is not None else None
