from dataclasses import dataclass

from app.db.models import Direction
from app.execution.base import ExecutionAdapter, OpenPositionSnapshot, OrderRequest, OrderResult


@dataclass
class _PaperPosition:
    client_order_id: str
    instrument: str
    direction: Direction
    size: float
    entry_price: float
    stop_loss: float
    take_profit: float


class PaperAdapter(ExecutionAdapter):
    """Fully functional in-memory paper-trading simulator: real spread/slippage/
    commission modeling, idempotent order handling, SL/TP fills off streamed
    prices. This is what Phase 4's "paper/demo trading only" build step (Build
    Order, item 3) runs against before any live broker adapter is wired in.
    """

    name = "paper"

    def __init__(
        self,
        starting_balance: float,
        spread_pct: float = 0.0002,
        slippage_pct: float = 0.0001,
        commission_pct: float = 0.0,
    ):
        self.balance = starting_balance
        self.spread_pct = spread_pct
        self.slippage_pct = slippage_pct
        self.commission_pct = commission_pct
        self._positions: dict[str, _PaperPosition] = {}
        self._closed_order_ids: set[str] = set()
        self._last_price: dict[str, float] = {}

    def update_price(self, instrument: str, price: float) -> None:
        self._last_price[instrument] = price

    def place_order(self, request: OrderRequest) -> OrderResult:
        if request.client_order_id in self._positions or request.client_order_id in self._closed_order_ids:
            # Idempotency: a retried request with the same client_order_id never double-fills.
            existing = self._positions.get(request.client_order_id)
            if existing:
                return OrderResult(request.client_order_id, request.client_order_id, "filled", existing.entry_price)
            return OrderResult(request.client_order_id, request.client_order_id, "rejected", error="duplicate_client_order_id")

        base_price = request.price if request.price is not None else self._last_price.get(request.instrument)
        if base_price is None:
            return OrderResult(request.client_order_id, None, "rejected", error="no_reference_price")

        spread_half = base_price * self.spread_pct / 2
        slip = base_price * self.slippage_pct
        if request.direction == Direction.long:
            fill_price = base_price + spread_half + slip
        else:
            fill_price = base_price - spread_half - slip

        self._positions[request.client_order_id] = _PaperPosition(
            client_order_id=request.client_order_id,
            instrument=request.instrument,
            direction=request.direction,
            size=request.size,
            entry_price=fill_price,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
        )
        self.balance -= request.size * fill_price * self.commission_pct
        self._last_price[request.instrument] = base_price
        return OrderResult(request.client_order_id, request.client_order_id, "filled", fill_price)

    def modify_order(self, client_order_id: str, *, stop_loss: float | None = None, take_profit: float | None = None) -> OrderResult:
        position = self._positions.get(client_order_id)
        if position is None:
            return OrderResult(client_order_id, None, "error", error="position_not_found")
        if stop_loss is not None:
            position.stop_loss = stop_loss
        if take_profit is not None:
            position.take_profit = take_profit
        return OrderResult(client_order_id, client_order_id, "filled", position.entry_price)

    def close_position(self, client_order_id: str) -> OrderResult:
        position = self._positions.pop(client_order_id, None)
        if position is None:
            return OrderResult(client_order_id, None, "error", error="position_not_found")
        price = self._last_price.get(position.instrument, position.entry_price)
        self._realize(position, price)
        self._closed_order_ids.add(client_order_id)
        return OrderResult(client_order_id, client_order_id, "filled", price)

    def check_stops(self, instrument: str, low: float, high: float) -> list[OrderResult]:
        """Called on each new candle for `instrument`: close any position whose
        stop-loss or take-profit was touched within [low, high]. Stop-loss is
        checked before take-profit within the same candle (conservative fill
        assumption for backtesting/paper — avoids overstating win rate)."""
        results: list[OrderResult] = []
        for client_order_id in [cid for cid, p in self._positions.items() if p.instrument == instrument]:
            position = self._positions[client_order_id]
            hit_price: float | None = None
            if position.direction == Direction.long:
                if low <= position.stop_loss:
                    hit_price = position.stop_loss
                elif high >= position.take_profit:
                    hit_price = position.take_profit
            else:
                if high >= position.stop_loss:
                    hit_price = position.stop_loss
                elif low <= position.take_profit:
                    hit_price = position.take_profit

            if hit_price is not None:
                self._realize(position, hit_price)
                del self._positions[client_order_id]
                self._closed_order_ids.add(client_order_id)
                results.append(OrderResult(client_order_id, client_order_id, "filled", hit_price))
        return results

    def _realize(self, position: _PaperPosition, exit_price: float) -> None:
        direction_mult = 1 if position.direction == Direction.long else -1
        pnl = (exit_price - position.entry_price) * direction_mult * position.size
        self.balance += pnl
        self.balance -= position.size * exit_price * self.commission_pct

    def get_open_positions(self) -> list[OpenPositionSnapshot]:
        snapshots = []
        for position in self._positions.values():
            last = self._last_price.get(position.instrument, position.entry_price)
            direction_mult = 1 if position.direction == Direction.long else -1
            unrealized = (last - position.entry_price) * direction_mult * position.size
            snapshots.append(
                OpenPositionSnapshot(
                    instrument=position.instrument,
                    direction=position.direction,
                    size=position.size,
                    entry_price=position.entry_price,
                    stop_loss=position.stop_loss,
                    take_profit=position.take_profit,
                    unrealized_pnl=unrealized,
                    client_order_id=position.client_order_id,
                    # The simulator has no separate broker-side ticket; the
                    # client order id doubles as the position identity.
                    broker_position_id=position.client_order_id,
                )
            )
        return snapshots

    def get_equity(self) -> float:
        unrealized = sum(p.unrealized_pnl for p in self.get_open_positions())
        return self.balance + unrealized
