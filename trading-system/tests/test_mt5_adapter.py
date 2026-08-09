import pytest

from app.db.models import Direction
from app.execution.base import OrderRequest
from app.execution.mt5_adapter import SYSTEM_MAGIC, MT5Adapter
from tests import fake_mt5


@pytest.fixture(autouse=True)
def _reset_fake():
    fake_mt5.reset()
    yield
    fake_mt5.reset()


def _adapter() -> MT5Adapter:
    return MT5Adapter(mt5_module=fake_mt5)


# 10,000 units of EURUSD = 0.10 lots at the standard 100,000 contract size.
# `size` is in INSTRUMENT UNITS, which is what the risk manager produces
# (size = risk_amount / stop_distance); the adapter converts to lots.
STANDARD_UNITS = 10_000.0


def _request(client_order_id="c1", instrument="EURUSD", direction=Direction.long, size=STANDARD_UNITS):
    return OrderRequest(
        client_order_id=client_order_id,
        instrument=instrument,
        direction=direction,
        size=size,
        stop_loss=1.09,
        take_profit=1.12,
    )


def test_place_order_fills_and_returns_broker_position_id():
    fake_mt5.add_symbol("EURUSD", 1.10)
    adapter = _adapter()

    result = adapter.place_order(_request())

    assert result.status == "filled"
    assert result.broker_position_id is not None
    assert len(adapter.get_open_positions()) == 1


def test_close_position_works_when_broker_strips_the_comment():
    """The bug this guards: MT5Adapter used to find its own positions by
    matching the client_order_id embedded in the order comment. Brokers
    routinely strip that field, which silently broke close/modify on live
    accounts. Identity must come from the broker position ticket instead."""
    fake_mt5.add_symbol("EURUSD", 1.10)
    fake_mt5.state.strip_comments = True
    adapter = _adapter()

    placed = adapter.place_order(_request())
    assert placed.status == "filled"
    assert placed.broker_position_id is not None

    closed = adapter.close_position(placed.broker_position_id)

    assert closed.status == "filled"
    assert adapter.get_open_positions() == []


def test_modify_order_works_when_broker_strips_the_comment():
    fake_mt5.add_symbol("EURUSD", 1.10)
    fake_mt5.state.strip_comments = True
    adapter = _adapter()
    placed = adapter.place_order(_request())

    result = adapter.modify_order(placed.broker_position_id, stop_loss=1.095)

    assert result.status == "filled"
    position = fake_mt5.state.positions[int(placed.broker_position_id)]
    assert position.sl == 1.095


def test_orders_are_stamped_with_the_system_magic_number():
    fake_mt5.add_symbol("EURUSD", 1.10)
    adapter = _adapter()
    placed = adapter.place_order(_request())
    position = fake_mt5.state.positions[int(placed.broker_position_id)]
    assert position.magic == SYSTEM_MAGIC


def test_manual_terminal_positions_are_invisible_and_untouchable():
    """A trade you opened by hand must never be sized against by the risk
    manager or closed by the system."""
    fake_mt5.add_symbol("EURUSD", 1.10)
    manual_ticket = fake_mt5.add_manual_position("EURUSD", magic=0)
    adapter = _adapter()

    assert adapter.get_open_positions() == []

    result = adapter.close_position(str(manual_ticket))
    assert result.status == "error"
    assert result.error == "position_not_found"
    assert manual_ticket in fake_mt5.state.positions  # still open, untouched


def test_place_order_is_idempotent_on_client_order_id():
    """A lost reply must not double the position on retry."""
    fake_mt5.add_symbol("EURUSD", 1.10)
    adapter = _adapter()

    first = adapter.place_order(_request(client_order_id="same-id"))
    second = adapter.place_order(_request(client_order_id="same-id"))

    assert first.status == "filled"
    assert second.status == "filled"
    assert len(adapter.get_open_positions()) == 1
    assert second.broker_position_id == first.broker_position_id


def test_disconnected_broker_refuses_to_place_orders():
    fake_mt5.add_symbol("EURUSD", 1.10)
    adapter = _adapter()
    fake_mt5.state.broker_connected = False

    result = adapter.place_order(_request())

    assert result.status == "error"
    assert "disconnected" in result.error
    assert fake_mt5.state.positions == {}  # fail closed: nothing sent


def test_is_connected_detects_terminal_up_but_broker_link_down():
    fake_mt5.add_symbol("EURUSD", 1.10)
    adapter = _adapter()
    assert adapter.is_connected() is True

    fake_mt5.state.broker_connected = False
    assert adapter.is_connected() is False


def test_reconnect_recovers_after_a_transient_drop():
    fake_mt5.add_symbol("EURUSD", 1.10)
    adapter = _adapter()

    fake_mt5.state.broker_connected = False
    assert adapter.ensure_connected() is False

    fake_mt5.state.broker_connected = True
    assert adapter.ensure_connected() is True
    assert adapter.place_order(_request()).status == "filled"


def test_is_tradeable_reflects_market_hours():
    fake_mt5.add_symbol("EURUSD", 1.10, tradeable=True)
    adapter = _adapter()
    assert adapter.is_tradeable("EURUSD") is True

    fake_mt5.set_tradeable("EURUSD", False)  # e.g. weekend
    assert adapter.is_tradeable("EURUSD") is False


def test_is_tradeable_is_false_when_disconnected():
    """Unknown state must never read as tradeable."""
    fake_mt5.add_symbol("EURUSD", 1.10)
    adapter = _adapter()
    fake_mt5.state.broker_connected = False
    assert adapter.is_tradeable("EURUSD") is False


def test_unknown_symbol_is_not_tradeable():
    fake_mt5.add_symbol("EURUSD", 1.10)
    adapter = _adapter()
    assert adapter.is_tradeable("NOTREAL") is False


def test_get_equity_raises_when_disconnected():
    fake_mt5.add_symbol("EURUSD", 1.10)
    adapter = _adapter()
    fake_mt5.state.broker_connected = False
    with pytest.raises(RuntimeError, match="disconnected"):
        adapter.get_equity()


def test_rejected_order_reports_rejection_without_opening_a_position():
    fake_mt5.add_symbol("EURUSD", 1.10)
    adapter = _adapter()
    fake_mt5.state.reject_orders = True

    result = adapter.place_order(_request())

    assert result.status == "rejected"
    assert adapter.get_open_positions() == []
