"""A fake MetaTrader5 module good enough to exercise MT5Adapter/MT5Provider.

The real `MetaTrader5` package is Windows-only, so without this the entire MT5
path would ship untested and only fail once it was pointed at a live account.
This models the behaviors that actually bite in production:

- brokers stripping/rewriting the order `comment` field (`strip_comments`)
- the terminal being up while the broker link is down (`broker_connected`)
- symbols going untradeable outside market hours (`set_tradeable`)
- positions opened by hand in the terminal, which carry a different magic
  number and must never be touched by the system
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

# --- constants mirroring the real module ---
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
TRADE_ACTION_DEAL = 1
TRADE_ACTION_SLTP = 2
ORDER_FILLING_IOC = 1
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_REJECT = 10006
SYMBOL_TRADE_MODE_DISABLED = 0
SYMBOL_TRADE_MODE_FULL = 4

TIMEFRAME_M1 = 1
TIMEFRAME_M5 = 5
TIMEFRAME_M15 = 15
TIMEFRAME_M30 = 30
TIMEFRAME_H1 = 16385
TIMEFRAME_H4 = 16388
TIMEFRAME_D1 = 16408


@dataclass
class _Position:
    ticket: int
    symbol: str
    type: int
    volume: float
    price_open: float
    sl: float
    tp: float
    profit: float
    magic: int
    comment: str


@dataclass
class _OrderSendResult:
    retcode: int
    order: int = 0
    position: int = 0
    price: float = 0.0
    comment: str = ""


@dataclass
class _SymbolInfo:
    name: str
    trade_mode: int
    visible: bool = True


@dataclass
class _Tick:
    bid: float
    ask: float
    time: int = 0


@dataclass
class _AccountInfo:
    equity: float
    balance: float


@dataclass
class _TerminalInfo:
    connected: bool


@dataclass
class _Deal:
    ticket: int
    position_id: int
    price: float
    time: int


@dataclass
class _State:
    """Mutable test-controlled state. Reset via `reset()` between tests."""

    initialized: bool = False
    initialize_should_fail: bool = False
    broker_connected: bool = True
    terminal_present: bool = True
    strip_comments: bool = False
    reject_orders: bool = False
    next_ticket: int = 1000
    positions: dict[int, _Position] = field(default_factory=dict)
    prices: dict[str, float] = field(default_factory=dict)
    tradeable: dict[str, bool] = field(default_factory=dict)
    equity: float = 10_000.0
    rates: dict[tuple[str, int], list[dict]] = field(default_factory=dict)
    last_error_value: tuple = (0, "ok")
    # Positions never returned by positions_get, to simulate a position that
    # closed (hit SL/TP) while the system was offline.
    removed_tickets: list[int] = field(default_factory=list)
    # position_id -> list of deals, for history_deals_get
    deals: dict[int, list] = field(default_factory=dict)
    history_available: bool = True


state = _State()


def reset() -> None:
    global state
    state = _State()


# --- module API ---


def initialize(**kwargs) -> bool:
    if state.initialize_should_fail:
        state.last_error_value = (-1, "initialize failed")
        return False
    state.initialized = True
    return True


def shutdown() -> None:
    state.initialized = False


def last_error():
    return state.last_error_value


def terminal_info():
    if not state.terminal_present:
        return None
    return _TerminalInfo(connected=state.broker_connected)


def account_info():
    if not state.broker_connected:
        return None
    return _AccountInfo(equity=state.equity, balance=state.equity)


def symbol_info(symbol: str):
    if symbol not in state.prices:
        return None
    tradeable = state.tradeable.get(symbol, True)
    return _SymbolInfo(
        name=symbol,
        trade_mode=SYMBOL_TRADE_MODE_FULL if tradeable else SYMBOL_TRADE_MODE_DISABLED,
    )


def symbol_select(symbol: str, enable: bool) -> bool:
    return symbol in state.prices


def symbol_info_tick(symbol: str):
    price = state.prices.get(symbol)
    if price is None:
        return None
    spread = price * 0.0001
    return _Tick(bid=price - spread / 2, ask=price + spread / 2, time=int(datetime.now(UTC).timestamp()))


def positions_get(**kwargs):
    return [p for t, p in state.positions.items() if t not in state.removed_tickets]


def order_send(request: dict):
    if state.reject_orders:
        return _OrderSendResult(retcode=TRADE_RETCODE_REJECT, comment="rejected by test")

    action = request.get("action")

    if action == TRADE_ACTION_SLTP:
        ticket = request["position"]
        pos = state.positions.get(ticket)
        if pos is None:
            return _OrderSendResult(retcode=TRADE_RETCODE_REJECT, comment="position not found")
        pos.sl = request.get("sl", pos.sl)
        pos.tp = request.get("tp", pos.tp)
        return _OrderSendResult(retcode=TRADE_RETCODE_DONE, order=ticket, position=ticket)

    if action != TRADE_ACTION_DEAL:
        return _OrderSendResult(retcode=TRADE_RETCODE_REJECT, comment="unsupported action")

    closing_ticket = request.get("position")
    if closing_ticket:
        pos = state.positions.pop(closing_ticket, None)
        if pos is None:
            return _OrderSendResult(retcode=TRADE_RETCODE_REJECT, comment="position not found")
        return _OrderSendResult(
            retcode=TRADE_RETCODE_DONE, order=closing_ticket, position=closing_ticket, price=request["price"]
        )

    ticket = state.next_ticket
    state.next_ticket += 1
    comment = "" if state.strip_comments else request.get("comment", "")
    state.positions[ticket] = _Position(
        ticket=ticket,
        symbol=request["symbol"],
        type=request["type"],
        volume=request["volume"],
        price_open=request["price"],
        sl=request.get("sl", 0.0),
        tp=request.get("tp", 0.0),
        profit=0.0,
        magic=request.get("magic", 0),
        comment=comment,
    )
    return _OrderSendResult(retcode=TRADE_RETCODE_DONE, order=ticket, position=ticket, price=request["price"])


def history_deals_get(**kwargs):
    if not state.history_available:
        return None
    position_id = kwargs.get("position")
    if position_id is None:
        return []
    return state.deals.get(int(position_id), [])


def copy_rates_from_pos(symbol: str, timeframe: int, start: int, count: int):
    key = (symbol, timeframe)
    if key in state.rates:
        return state.rates[key][-count:] if count else state.rates[key]
    price = state.prices.get(symbol)
    if price is None:
        return None
    base_time = datetime.now(UTC) - timedelta(hours=count)
    return [
        {
            "time": int((base_time + timedelta(hours=i)).timestamp()),
            "open": price,
            "high": price * 1.001,
            "low": price * 0.999,
            "close": price,
            "tick_volume": 100,
        }
        for i in range(count)
    ]


# --- test helpers ---


def add_symbol(symbol: str, price: float, *, tradeable: bool = True) -> None:
    state.prices[symbol] = price
    state.tradeable[symbol] = tradeable


def set_tradeable(symbol: str, tradeable: bool) -> None:
    state.tradeable[symbol] = tradeable


def close_position_externally(ticket: int, exit_price: float) -> None:
    """Simulate the broker closing a position (SL/TP hit) while the system was
    offline: it disappears from positions_get but leaves a closing deal in the
    history, which is what reconciliation reads to recover the real exit."""
    state.positions.pop(ticket, None)
    state.deals.setdefault(int(ticket), []).append(
        _Deal(ticket=ticket, position_id=int(ticket), price=exit_price, time=int(datetime.now(UTC).timestamp()))
    )


def add_manual_position(symbol: str, *, magic: int = 0, volume: float = 1.0) -> int:
    """A position opened by hand in the terminal (different magic). The system
    must never see, size against, or close this."""
    ticket = state.next_ticket
    state.next_ticket += 1
    price = state.prices.get(symbol, 1.0)
    state.positions[ticket] = _Position(
        ticket=ticket,
        symbol=symbol,
        type=ORDER_TYPE_BUY,
        volume=volume,
        price_open=price,
        sl=0.0,
        tp=0.0,
        profit=0.0,
        magic=magic,
        comment="manual trade",
    )
    return ticket
