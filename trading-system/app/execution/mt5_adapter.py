import threading
from datetime import UTC, datetime

from app.config import settings
from app.db.models import Direction
from app.execution.base import ExecutionAdapter, OpenPositionSnapshot, OrderRequest, OrderResult

# Every order this system places is stamped with this magic number. Unlike the
# `comment` field (which brokers strip, truncate, or rewrite at will), magic is
# preserved by MT5 and is how we distinguish OUR positions from ones you opened
# by hand in the terminal — we must never close or modify a manual trade.
SYSTEM_MAGIC = 20260808


def _import_mt5():
    """Deferred, replaceable import.

    The real MetaTrader5 package only ships a working native extension on
    Windows. Isolating the import here means the whole adapter can be
    exercised against a fake module in tests on any platform (see
    tests/fake_mt5.py) instead of being untestable until it's on a live
    Windows box.
    """
    import MetaTrader5 as mt5

    return mt5


class MT5Adapter(ExecutionAdapter):
    """Live execution against a MetaTrader 5 terminal running on the same host.

    Requires the `trading-system[mt5]` extra and a Windows machine with the MT5
    terminal installed, logged in, and with "Algo Trading" enabled. Designed to
    survive the failures a long-running install actually hits: the terminal
    restarting, the broker dropping the connection at rollover, the laptop
    losing WiFi. Every public method re-establishes the connection if needed
    and reports failure rather than raising into the trading loop, so a blip
    costs one cycle instead of crashing the process.

    Do not point this at a funded account until Build Order steps 1-3
    (backtest -> walk-forward -> weeks of paper trading) have passed.
    """

    name = "mt5"

    def __init__(self, *, magic: int = SYSTEM_MAGIC, mt5_module=None):
        self._mt5 = mt5_module if mt5_module is not None else _import_mt5()
        self.magic = magic
        self._lock = threading.Lock()
        self._connected = False
        self._last_connect_error: str | None = None
        self._last_connected_at: datetime | None = None
        self.connect()

    # ---------------- connection management ----------------

    def connect(self) -> bool:
        """(Re)initialize the MT5 connection. Safe to call repeatedly."""
        with self._lock:
            return self._connect_locked()

    def _connect_locked(self) -> bool:
        mt5 = self._mt5
        kwargs = {}
        if settings.mt5_login:
            kwargs["login"] = int(settings.mt5_login)
        if settings.mt5_password:
            kwargs["password"] = settings.mt5_password
        if settings.mt5_server:
            kwargs["server"] = settings.mt5_server
        if settings.mt5_path:
            kwargs["path"] = settings.mt5_path

        try:
            ok = mt5.initialize(**kwargs)
        except Exception as exc:  # noqa: BLE001 - never let a broker/IPC fault crash the loop
            self._connected = False
            self._last_connect_error = str(exc)
            return False

        if not ok:
            self._connected = False
            self._last_connect_error = str(mt5.last_error())
            return False

        self._connected = True
        self._last_connect_error = None
        self._last_connected_at = datetime.now(UTC)
        return True

    def is_connected(self) -> bool:
        """True only if the terminal answers right now. `initialize()`
        succeeding once says nothing about the connection thirty minutes later,
        so this actively probes rather than trusting a cached flag."""
        mt5 = self._mt5
        try:
            info = mt5.terminal_info()
        except Exception:  # noqa: BLE001 - treat any IPC failure as disconnected
            info = None

        if info is None:
            self._connected = False
            return False

        # The terminal process can be up while the broker link is down.
        broker_connected = getattr(info, "connected", True)
        self._connected = bool(broker_connected)
        if self._connected:
            self._last_connected_at = datetime.now(UTC)
        return self._connected

    def ensure_connected(self) -> bool:
        """Probe, and attempt exactly one reconnect if the link is down.
        Returns whether the adapter is usable. Callers must treat False as
        "do not trade this cycle" — fail closed, never assume."""
        if self.is_connected():
            return True
        return self.connect() and self.is_connected()

    @property
    def last_connect_error(self) -> str | None:
        return self._last_connect_error

    @property
    def last_connected_at(self) -> datetime | None:
        return self._last_connected_at

    # ---------------- market hours / tradability ----------------

    def is_tradeable(self, instrument: str) -> bool:
        """Whether MT5 will accept an order on this symbol right now.

        This is what separates "market closed" from "something is broken":
        forex/metals go untradeable over the weekend and crypto CFDs during a
        broker's maintenance window, both entirely expected. Returns False
        when disconnected, since an unknown state must not be treated as
        tradeable.
        """
        if not self.ensure_connected():
            return False
        mt5 = self._mt5
        try:
            info = mt5.symbol_info(instrument)
        except Exception:  # noqa: BLE001
            return False
        if info is None:
            return False
        # Symbols not in Market Watch report stale/absent data until selected.
        if not getattr(info, "visible", True):
            try:
                mt5.symbol_select(instrument, True)
                info = mt5.symbol_info(instrument)
            except Exception:  # noqa: BLE001
                return False
            if info is None:
                return False
        full = getattr(mt5, "SYMBOL_TRADE_MODE_FULL", 4)
        return getattr(info, "trade_mode", full) == full

    # ---------------- orders ----------------

    def place_order(self, request: OrderRequest) -> OrderResult:
        if not self.ensure_connected():
            return OrderResult(request.client_order_id, None, "error", error=f"mt5_disconnected: {self._last_connect_error}")

        mt5 = self._mt5
        # Idempotency: if a position carrying this client_order_id already
        # exists, a previous attempt succeeded and only the reply was lost.
        # Re-sending would double the position.
        existing = self._find_position_by_client_order_id(request.client_order_id)
        if existing is not None:
            return OrderResult(
                request.client_order_id,
                str(existing.ticket),
                "filled",
                getattr(existing, "price_open", None),
                broker_position_id=str(existing.ticket),
            )

        try:
            tick = mt5.symbol_info_tick(request.instrument)
        except Exception as exc:  # noqa: BLE001
            return OrderResult(request.client_order_id, None, "error", error=f"tick_error: {exc}")
        if tick is None:
            return OrderResult(request.client_order_id, None, "rejected", error="no_tick_data")

        is_long = request.direction == Direction.long
        order_type = mt5.ORDER_TYPE_BUY if is_long else mt5.ORDER_TYPE_SELL
        price = tick.ask if is_long else tick.bid

        try:
            result = mt5.order_send(
                {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": request.instrument,
                    "volume": request.size,
                    "type": order_type,
                    "price": price,
                    "sl": request.stop_loss,
                    "tp": request.take_profit,
                    # magic is the reliable ownership marker; comment is
                    # best-effort human context only and is never matched on.
                    "magic": self.magic,
                    "comment": request.client_order_id[:31],
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
            )
        except Exception as exc:  # noqa: BLE001
            return OrderResult(request.client_order_id, None, "error", error=f"order_send_error: {exc}")

        if result is None:
            return OrderResult(request.client_order_id, None, "rejected", error=str(mt5.last_error()))
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            error = getattr(result, "comment", None) or f"retcode={result.retcode}"
            return OrderResult(request.client_order_id, None, "rejected", error=error)

        # order_send returns the deal/order ticket; the POSITION ticket is what
        # we need for later modify/close. On netting accounts they differ.
        position_id = getattr(result, "position", None) or None
        if not position_id:
            found = self._find_position_by_client_order_id(request.client_order_id)
            position_id = found.ticket if found is not None else getattr(result, "order", None)

        return OrderResult(
            request.client_order_id,
            str(getattr(result, "order", "")) or None,
            "filled",
            getattr(result, "price", None),
            broker_position_id=str(position_id) if position_id else None,
        )

    def modify_order(
        self, client_order_id: str, *, stop_loss: float | None = None, take_profit: float | None = None
    ) -> OrderResult:
        if not self.ensure_connected():
            return OrderResult(client_order_id, None, "error", error="mt5_disconnected")

        mt5 = self._mt5
        position = self._resolve_position(client_order_id)
        if position is None:
            return OrderResult(client_order_id, None, "error", error="position_not_found")

        try:
            result = mt5.order_send(
                {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "position": position.ticket,
                    "symbol": position.symbol,
                    "sl": stop_loss if stop_loss is not None else position.sl,
                    "tp": take_profit if take_profit is not None else position.tp,
                }
            )
        except Exception as exc:  # noqa: BLE001
            return OrderResult(client_order_id, None, "error", error=f"modify_error: {exc}")

        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            detail = str(mt5.last_error()) if result is None else f"retcode={result.retcode}"
            return OrderResult(client_order_id, None, "error", error=detail)
        return OrderResult(client_order_id, str(position.ticket), "filled", broker_position_id=str(position.ticket))

    def close_position(self, client_order_id: str) -> OrderResult:
        if not self.ensure_connected():
            return OrderResult(client_order_id, None, "error", error="mt5_disconnected")

        mt5 = self._mt5
        position = self._resolve_position(client_order_id)
        if position is None:
            return OrderResult(client_order_id, None, "error", error="position_not_found")

        try:
            tick = mt5.symbol_info_tick(position.symbol)
        except Exception as exc:  # noqa: BLE001
            return OrderResult(client_order_id, None, "error", error=f"tick_error: {exc}")
        if tick is None:
            return OrderResult(client_order_id, None, "error", error="no_tick_data")

        is_buy = position.type == mt5.ORDER_TYPE_BUY
        close_type = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY
        price = tick.bid if is_buy else tick.ask

        try:
            result = mt5.order_send(
                {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": position.symbol,
                    "volume": position.volume,
                    "type": close_type,
                    "position": position.ticket,
                    "price": price,
                    "magic": self.magic,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
            )
        except Exception as exc:  # noqa: BLE001
            return OrderResult(client_order_id, None, "error", error=f"close_error: {exc}")

        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            detail = str(mt5.last_error()) if result is None else f"retcode={result.retcode}"
            return OrderResult(client_order_id, None, "error", error=detail)
        return OrderResult(
            client_order_id,
            str(getattr(result, "order", "")) or None,
            "filled",
            getattr(result, "price", None),
            broker_position_id=str(position.ticket),
        )

    # ---------------- state ----------------

    def get_open_positions(self) -> list[OpenPositionSnapshot]:
        """Only positions carrying our magic number. Anything you opened by
        hand in the terminal is deliberately invisible here, so the risk
        manager never sizes against it and close_position can never touch it."""
        if not self.ensure_connected():
            return []
        return [
            OpenPositionSnapshot(
                instrument=pos.symbol,
                direction=Direction.long if pos.type == self._mt5.ORDER_TYPE_BUY else Direction.short,
                size=pos.volume,
                entry_price=pos.price_open,
                stop_loss=pos.sl,
                take_profit=pos.tp,
                unrealized_pnl=pos.profit,
                client_order_id=getattr(pos, "comment", "") or "",
                broker_position_id=str(pos.ticket),
            )
            for pos in self._own_positions()
        ]

    def get_closed_position_exit_price(self, broker_position_id: str) -> float | None:
        """The real fill price of the deal that closed a position, from MT5's
        deal history. Used by startup reconciliation for positions the broker
        closed (SL/TP hit) while this process was down. Returns None when the
        history isn't available, so the caller records nothing rather than
        guessing a price."""
        if not self.ensure_connected():
            return None
        mt5 = self._mt5
        try:
            deals = mt5.history_deals_get(position=int(broker_position_id))
        except Exception:  # noqa: BLE001
            return None
        if not deals:
            return None
        # The last deal on the position is the one that closed it.
        closing = max(deals, key=lambda d: getattr(d, "time", 0))
        price = getattr(closing, "price", None)
        return float(price) if price else None

    def get_equity(self) -> float:
        if not self.ensure_connected():
            raise RuntimeError(f"MT5 disconnected, cannot read equity: {self._last_connect_error}")
        info = self._mt5.account_info()
        if info is None:
            raise RuntimeError(f"MT5 account_info() failed: {self._mt5.last_error()}")
        return float(info.equity)

    # ---------------- internals ----------------

    def _own_positions(self) -> list:
        try:
            positions = self._mt5.positions_get()
        except Exception:  # noqa: BLE001
            return []
        return [p for p in (positions or []) if getattr(p, "magic", None) == self.magic]

    def _resolve_position(self, identifier: str):
        """Find one of our positions by broker ticket (preferred) or, as a
        fallback, by a comment still carrying the client_order_id. Ticket
        first because the comment may have been stripped by the broker."""
        for pos in self._own_positions():
            if str(pos.ticket) == str(identifier):
                return pos
        return self._find_position_by_client_order_id(identifier)

    def _find_position_by_client_order_id(self, client_order_id: str):
        truncated = client_order_id[:31]
        for pos in self._own_positions():
            if (getattr(pos, "comment", "") or "") == truncated:
                return pos
        return None
