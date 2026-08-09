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
ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2
# The bitmask flags a symbol reports in symbol_info().filling_mode. Note these
# are NOT the same numbers as the ORDER_FILLING_* constants above — conflating
# them is a real source of "every order rejected" bugs.
SYMBOL_FILLING_FOK = 1
SYMBOL_FILLING_IOC = 2
SYMBOL_FILLING_RETURN = 4
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_REJECT = 10006
TRADE_RETCODE_INVALID_VOLUME = 10014
TRADE_RETCODE_INVALID_STOPS = 10016
TRADE_RETCODE_INVALID_FILL = 10030
SYMBOL_TRADE_MODE_DISABLED = 0
SYMBOL_TRADE_MODE_FULL = 4
ACCOUNT_TRADE_MODE_DEMO = 0
ACCOUNT_TRADE_MODE_CONTEST = 1
ACCOUNT_TRADE_MODE_REAL = 2

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
    # Contract terms. Defaults describe a standard FX pair; per-symbol overrides
    # live in state.specs so a test can model a crypto CFD or an exotic lot step.
    trade_contract_size: float = 100_000.0
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01
    digits: int = 5
    point: float = 0.00001
    trade_stops_level: int = 0
    filling_mode: int = SYMBOL_FILLING_FOK | SYMBOL_FILLING_IOC


@dataclass
class _SymbolSpecOverride:
    """Per-symbol contract terms a test wants to differ from the FX defaults."""

    trade_contract_size: float | None = None
    volume_min: float | None = None
    volume_max: float | None = None
    volume_step: float | None = None
    digits: int | None = None
    point: float | None = None
    trade_stops_level: int | None = None
    filling_mode: int | None = None


@dataclass
class _Tick:
    bid: float
    ask: float
    time: int = 0


@dataclass
class _AccountInfo:
    equity: float
    balance: float
    login: int = 1234567
    server: str = "FakeBroker-Demo"
    currency: str = "USD"
    trade_mode: int = 0  # ACCOUNT_TRADE_MODE_DEMO


@dataclass
class _TerminalInfo:
    connected: bool
    trade_allowed: bool = True
    build: int = 4000


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
    specs: dict[str, "_SymbolSpecOverride"] = field(default_factory=dict)
    # Volumes order_send was asked for, in call order. Lets a test assert what
    # actually reached the broker rather than only whether it succeeded.
    sent_volumes: list[float] = field(default_factory=list)
    sent_requests: list[dict] = field(default_factory=list)
    equity: float = 10_000.0
    # Two switches the verifier script depends on: algo trading can be off
    # in the terminal, and the account can be funded rather than demo.
    algo_trading_allowed: bool = True
    account_trade_mode: int = ACCOUNT_TRADE_MODE_DEMO
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
    return _TerminalInfo(connected=state.broker_connected, trade_allowed=state.algo_trading_allowed)


def account_info():
    if not state.broker_connected:
        return None
    return _AccountInfo(equity=state.equity, balance=state.equity, trade_mode=state.account_trade_mode)


def set_symbol_spec(symbol: str, **overrides) -> None:
    """Give a symbol non-default contract terms, e.g.
    `set_symbol_spec("BTCUSD", trade_contract_size=1.0, volume_step=0.01)`."""
    state.specs[symbol] = _SymbolSpecOverride(**overrides)


def symbol_info(symbol: str):
    if symbol not in state.prices:
        return None
    tradeable = state.tradeable.get(symbol, True)
    info = _SymbolInfo(
        name=symbol,
        trade_mode=SYMBOL_TRADE_MODE_FULL if tradeable else SYMBOL_TRADE_MODE_DISABLED,
    )
    override = state.specs.get(symbol)
    if override is not None:
        for f, v in vars(override).items():
            if v is not None:
                setattr(info, f, v)
    return info


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


def _check_contract_terms(request: dict):
    """Reject the way a real broker does. Without these checks the fake accepts
    anything, which is exactly how a unit mismatch and a hardcoded filling mode
    both shipped untested.
    """
    info = symbol_info(request["symbol"])
    if info is None:
        return _OrderSendResult(retcode=TRADE_RETCODE_REJECT, comment="unknown symbol")

    volume = request.get("volume")
    if volume is None or volume < info.volume_min or volume > info.volume_max:
        return _OrderSendResult(
            retcode=TRADE_RETCODE_INVALID_VOLUME,
            comment=f"invalid volume {volume} (min {info.volume_min}, max {info.volume_max})",
        )
    steps = volume / info.volume_step
    if abs(steps - round(steps)) > 1e-6:
        return _OrderSendResult(
            retcode=TRADE_RETCODE_INVALID_VOLUME, comment=f"volume {volume} not a multiple of {info.volume_step}"
        )

    requested_fill = request.get("type_filling")
    accepted = [
        mode
        for flag, mode in (
            (SYMBOL_FILLING_FOK, ORDER_FILLING_FOK),
            (SYMBOL_FILLING_IOC, ORDER_FILLING_IOC),
            (SYMBOL_FILLING_RETURN, ORDER_FILLING_RETURN),
        )
        if info.filling_mode & flag
    ]
    if accepted and requested_fill not in accepted:
        return _OrderSendResult(
            retcode=TRADE_RETCODE_INVALID_FILL, comment=f"filling mode {requested_fill} not supported"
        )

    minimum = info.trade_stops_level * info.point
    if minimum > 0:
        price = request.get("price") or 0.0
        for level in (request.get("sl"), request.get("tp")):
            if level and abs(price - level) < minimum:
                return _OrderSendResult(
                    retcode=TRADE_RETCODE_INVALID_STOPS, comment=f"stop within {minimum} of price"
                )
    return None


def order_send(request: dict):
    state.sent_requests.append(dict(request))
    if "volume" in request:
        state.sent_volumes.append(request["volume"])
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

    rejection = _check_contract_terms(request)
    if rejection is not None:
        return rejection

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


def add_symbol(symbol: str, price: float, *, tradeable: bool = True, **spec) -> None:
    """Register a tradeable symbol. Extra kwargs override the FX-default
    contract terms, e.g. a crypto CFD: `add_symbol("BTCUSD", 64000,
    trade_contract_size=1.0, digits=2, point=0.01)`."""
    state.prices[symbol] = price
    state.tradeable[symbol] = tradeable
    if spec:
        set_symbol_spec(symbol, **spec)


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
