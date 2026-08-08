from app.config import settings
from app.db.models import Direction
from app.execution.base import ExecutionAdapter, OpenPositionSnapshot, OrderRequest, OrderResult


class MT5Adapter(ExecutionAdapter):
    """Live forex/indices/metals execution via the MetaTrader5 Python API.

    Requires the `trading-system[mt5]` extra and a Windows host (see
    app/data/providers/mt5_provider.py for why). Do not point this at a live
    account until Build Order steps 1-3 (backtest -> walk-forward -> weeks of
    paper trading) have passed for the strategies in question.
    """

    name = "mt5"

    def __init__(self):
        try:
            import MetaTrader5 as mt5
        except ImportError as exc:
            raise RuntimeError(
                "MetaTrader5 package not available on this platform. Use PaperAdapter "
                "for development/backtesting, or run this adapter on a Windows host."
            ) from exc

        self._mt5 = mt5
        if not mt5.initialize(
            login=int(settings.mt5_login) if settings.mt5_login else None,
            password=settings.mt5_password or None,
            server=settings.mt5_server or None,
            path=settings.mt5_path or None,
        ):
            raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")

    def place_order(self, request: OrderRequest) -> OrderResult:
        mt5 = self._mt5
        order_type = mt5.ORDER_TYPE_BUY if request.direction == Direction.long else mt5.ORDER_TYPE_SELL
        tick = mt5.symbol_info_tick(request.instrument)
        if tick is None:
            return OrderResult(request.client_order_id, None, "rejected", error="no_tick_data")
        price = tick.ask if request.direction == Direction.long else tick.bid

        result = mt5.order_send(
            {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": request.instrument,
                "volume": request.size,
                "type": order_type,
                "price": price,
                "sl": request.stop_loss,
                "tp": request.take_profit,
                # MT5 has no native client-order-id field on the deal action; the
                # comment field carries it so a crashed/retried caller can look up
                # whether this request already went through before resubmitting.
                "comment": request.client_order_id[:31],
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
        )
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            error = getattr(result, "comment", str(mt5.last_error()))
            return OrderResult(request.client_order_id, None, "rejected", error=error)

        return OrderResult(request.client_order_id, str(result.order), "filled", result.price)

    def modify_order(self, client_order_id: str, *, stop_loss: float | None = None, take_profit: float | None = None) -> OrderResult:
        mt5 = self._mt5
        position = self._find_position(client_order_id)
        if position is None:
            return OrderResult(client_order_id, None, "error", error="position_not_found")

        result = mt5.order_send(
            {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": position.ticket,
                "symbol": position.symbol,
                "sl": stop_loss if stop_loss is not None else position.sl,
                "tp": take_profit if take_profit is not None else position.tp,
            }
        )
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            return OrderResult(client_order_id, None, "error", error=str(mt5.last_error()))
        return OrderResult(client_order_id, str(position.ticket), "filled")

    def close_position(self, client_order_id: str) -> OrderResult:
        mt5 = self._mt5
        position = self._find_position(client_order_id)
        if position is None:
            return OrderResult(client_order_id, None, "error", error="position_not_found")

        tick = mt5.symbol_info_tick(position.symbol)
        is_buy = position.type == mt5.ORDER_TYPE_BUY
        close_type = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY
        price = tick.bid if is_buy else tick.ask

        result = mt5.order_send(
            {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": position.symbol,
                "volume": position.volume,
                "type": close_type,
                "position": position.ticket,
                "price": price,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
        )
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            return OrderResult(client_order_id, None, "error", error=str(mt5.last_error()))
        return OrderResult(client_order_id, str(result.order), "filled", result.price)

    def get_open_positions(self) -> list[OpenPositionSnapshot]:
        mt5 = self._mt5
        positions = mt5.positions_get() or []
        snapshots = []
        for pos in positions:
            direction = Direction.long if pos.type == mt5.ORDER_TYPE_BUY else Direction.short
            snapshots.append(
                OpenPositionSnapshot(
                    instrument=pos.symbol,
                    direction=direction,
                    size=pos.volume,
                    entry_price=pos.price_open,
                    stop_loss=pos.sl,
                    take_profit=pos.tp,
                    unrealized_pnl=pos.profit,
                    client_order_id=pos.comment,
                )
            )
        return snapshots

    def get_equity(self) -> float:
        info = self._mt5.account_info()
        if info is None:
            raise RuntimeError(f"MT5 account_info() failed: {self._mt5.last_error()}")
        return float(info.equity)

    def _find_position(self, client_order_id: str):
        positions = self._mt5.positions_get() or []
        for pos in positions:
            if pos.comment == client_order_id[:31]:
                return pos
        return None
