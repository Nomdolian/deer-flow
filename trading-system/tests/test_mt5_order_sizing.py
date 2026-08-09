"""The MT5 order path's contract-terms handling.

Every case here is a way a real broker rejects every single order — or, in the
volume case, accepts a catastrophically wrong one. None of it was covered until
the fake broker was made strict enough to reject like a real one.
"""

import pytest

from app.config import settings
from app.db.models import Direction
from app.execution.base import OrderRequest
from app.execution.mt5_adapter import MT5Adapter
from tests import fake_mt5

CRYPTO_CFD = {"trade_contract_size": 1.0, "digits": 2, "point": 0.01, "volume_min": 0.01, "volume_step": 0.01}


@pytest.fixture(autouse=True)
def _reset_fake():
    fake_mt5.reset()
    yield
    fake_mt5.reset()


def _adapter() -> MT5Adapter:
    return MT5Adapter(mt5_module=fake_mt5)


def _request(size, instrument="EURUSD", stop_loss=1.09, take_profit=1.12):
    return OrderRequest(
        client_order_id="c1",
        instrument=instrument,
        direction=Direction.long,
        size=size,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )


# ---------------------------------------------------------------- units -> lots


def test_size_in_units_becomes_lots_not_passed_through_raw():
    """The bug this guards: the risk manager sizes in instrument units
    (size = risk_amount / stop_distance) but MT5's `volume` is in LOTS. Passing
    the size straight through asked for a position 100,000x too large — a
    typical EURUSD size of ~15,000 units would have been sent as 15,000 lots,
    or 1.5 billion units of currency."""
    fake_mt5.add_symbol("EURUSD", 1.10)
    adapter = _adapter()

    result = adapter.place_order(_request(15_000.0))

    assert result.status == "filled"
    assert fake_mt5.state.sent_volumes == [0.15]  # 15,000 / 100,000, not 15,000


def test_crypto_contract_size_is_read_per_symbol():
    """One lot of a BTC CFD is one coin, not 100,000 of them. The contract size
    has to come from the symbol, never a constant."""
    fake_mt5.add_symbol("BTCUSD", 64_000.0, **CRYPTO_CFD)
    adapter = _adapter()

    result = adapter.place_order(_request(0.05, instrument="BTCUSD", stop_loss=63_000.0, take_profit=66_000.0))

    assert result.status == "filled"
    assert fake_mt5.state.sent_volumes == [0.05]


# ---------------------------------------------------------------- volume rules


def test_volume_rounds_down_to_the_brokers_lot_step():
    """Down, never up: the risk manager authorized a specific dollar risk, and
    rounding a lot size up spends more than it approved."""
    fake_mt5.add_symbol("EURUSD", 1.10, volume_step=0.01)
    adapter = _adapter()

    adapter.place_order(_request(17_900.0))  # 0.179 lots

    assert fake_mt5.state.sent_volumes == [0.17]  # not 0.18


def test_size_below_the_broker_minimum_is_rejected_not_rounded_up():
    """Rounding up to the broker's minimum would take more risk than approved.
    Skipping is the correct answer, with a reason that names the real cause."""
    fake_mt5.add_symbol("EURUSD", 1.10, volume_min=0.01)
    adapter = _adapter()

    result = adapter.place_order(_request(500.0))  # 0.005 lots, under the 0.01 minimum

    assert result.status == "rejected"
    assert "size_below_broker_minimum" in result.error
    # The message must quote the UNROUNDED requirement. Quoting the floored
    # value prints "needs 0 lots", which says nothing about how far short the
    # account is — and that shortfall is the whole decision the operator faces.
    assert "0.005 lots" in result.error
    assert "minimum is 0.01" in result.error
    assert "2.0x larger" in result.error
    assert fake_mt5.state.positions == {}


def test_size_above_the_broker_maximum_is_clamped_down():
    """Clamping down only reduces risk, so the trade can still proceed."""
    fake_mt5.add_symbol("EURUSD", 1.10, volume_max=5.0)
    adapter = _adapter()

    result = adapter.place_order(_request(2_000_000.0))  # 20 lots, over the 5.0 cap

    assert result.status == "filled"
    assert fake_mt5.state.sent_volumes == [5.0]


# ---------------------------------------------------------------- filling mode


def test_filling_mode_comes_from_the_symbol_not_a_hardcoded_constant():
    """A broker that only accepts FOK rejects every IOC order with
    TRADE_RETCODE_INVALID_FILL. The adapter used to hardcode IOC."""
    fake_mt5.add_symbol("EURUSD", 1.10, filling_mode=fake_mt5.SYMBOL_FILLING_FOK)
    adapter = _adapter()

    result = adapter.place_order(_request(15_000.0))

    assert result.status == "filled"
    assert fake_mt5.state.sent_requests[0]["type_filling"] == fake_mt5.ORDER_FILLING_FOK


def test_ioc_is_used_when_fok_is_not_offered():
    fake_mt5.add_symbol("EURUSD", 1.10, filling_mode=fake_mt5.SYMBOL_FILLING_IOC)
    adapter = _adapter()

    assert adapter.place_order(_request(15_000.0)).status == "filled"
    assert fake_mt5.state.sent_requests[0]["type_filling"] == fake_mt5.ORDER_FILLING_IOC


def test_return_filling_is_used_when_it_is_the_only_option():
    fake_mt5.add_symbol("EURUSD", 1.10, filling_mode=fake_mt5.SYMBOL_FILLING_RETURN)
    adapter = _adapter()

    assert adapter.place_order(_request(15_000.0)).status == "filled"
    assert fake_mt5.state.sent_requests[0]["type_filling"] == fake_mt5.ORDER_FILLING_RETURN


# ---------------------------------------------------------------- stops & price


def test_stops_inside_the_brokers_minimum_distance_are_rejected_not_widened():
    """Widening a stop to satisfy the broker silently increases the loss the
    risk manager sized for, so the trade is skipped instead."""
    fake_mt5.add_symbol("EURUSD", 1.10, trade_stops_level=100, point=0.0001)  # 100 points = 0.01
    adapter = _adapter()

    result = adapter.place_order(_request(15_000.0, stop_loss=1.0995, take_profit=1.12))

    assert result.status == "rejected"
    assert "stop_loss_too_close_to_price" in result.error
    assert fake_mt5.state.positions == {}


def test_prices_are_rounded_to_the_symbols_digits():
    fake_mt5.add_symbol("BTCUSD", 64_000.0, **CRYPTO_CFD)
    adapter = _adapter()

    adapter.place_order(_request(0.05, instrument="BTCUSD", stop_loss=63_000.123456, take_profit=66_000.987654))

    sent = fake_mt5.state.sent_requests[0]
    assert sent["sl"] == 63_000.12
    assert sent["tp"] == 66_000.99


def test_orders_carry_a_slippage_tolerance():
    """A deviation of 0 makes the broker reject any fill that moved between
    quote and execution, which on a fast market is most of them."""
    fake_mt5.add_symbol("EURUSD", 1.10)
    adapter = _adapter()

    adapter.place_order(_request(15_000.0))

    assert fake_mt5.state.sent_requests[0]["deviation"] == settings.mt5_slippage_points
    assert settings.mt5_slippage_points > 0


# ---------------------------------------------------------------- spec plumbing


def test_resolve_volume_reports_why_it_could_not_size():
    fake_mt5.add_symbol("EURUSD", 1.10)
    adapter = _adapter()

    lots, error = adapter.resolve_volume("EURUSD", 15_000.0)
    assert (lots, error) == (0.15, None)

    lots, error = adapter.resolve_volume("NOPE", 15_000.0)
    assert lots is None
    assert "symbol_spec_unavailable" in error


def test_closing_reuses_the_symbols_filling_mode():
    """The close path had the same hardcoded IOC, so on an FOK-only broker
    positions could be opened but never closed by the system."""
    fake_mt5.add_symbol("EURUSD", 1.10, filling_mode=fake_mt5.SYMBOL_FILLING_FOK)
    adapter = _adapter()
    placed = adapter.place_order(_request(15_000.0))

    closed = adapter.close_position(placed.broker_position_id)

    assert closed.status == "filled"
    assert fake_mt5.state.sent_requests[-1]["type_filling"] == fake_mt5.ORDER_FILLING_FOK
