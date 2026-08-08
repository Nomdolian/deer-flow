from app.db.models import Direction
from app.execution.base import OrderRequest
from app.execution.paper_adapter import PaperAdapter


def test_idempotent_duplicate_order_does_not_double_fill():
    adapter = PaperAdapter(starting_balance=10_000)
    adapter.update_price("EURUSD", 1.1000)
    request = OrderRequest("order-1", "EURUSD", Direction.long, size=1000, stop_loss=1.0950, take_profit=1.1100)

    first = adapter.place_order(request)
    second = adapter.place_order(request)

    assert first.status == "filled"
    assert second.status == "filled"
    assert len(adapter.get_open_positions()) == 1  # not 2


def test_spread_and_slippage_worsen_fill_price_for_buys():
    zero_cost = PaperAdapter(starting_balance=10_000, spread_pct=0, slippage_pct=0)
    with_cost = PaperAdapter(starting_balance=10_000, spread_pct=0.001, slippage_pct=0.0005)
    zero_cost.update_price("EURUSD", 1.1000)
    with_cost.update_price("EURUSD", 1.1000)

    request = OrderRequest("o1", "EURUSD", Direction.long, size=1000, stop_loss=1.09, take_profit=1.12)
    a = zero_cost.place_order(request)
    b = with_cost.place_order(request)
    assert b.filled_price > a.filled_price


def test_stop_loss_closes_position_and_realizes_loss():
    adapter = PaperAdapter(starting_balance=10_000, spread_pct=0, slippage_pct=0)
    adapter.update_price("EURUSD", 1.1000)
    adapter.place_order(OrderRequest("o1", "EURUSD", Direction.long, size=10_000, stop_loss=1.0950, take_profit=1.1100))

    starting_equity = adapter.get_equity()
    fills = adapter.check_stops("EURUSD", low=1.0940, high=1.1010)
    assert len(fills) == 1
    assert fills[0].filled_price == 1.0950
    assert adapter.get_equity() < starting_equity
    assert adapter.get_open_positions() == []


def test_take_profit_closes_position_and_realizes_gain():
    adapter = PaperAdapter(starting_balance=10_000, spread_pct=0, slippage_pct=0)
    adapter.update_price("EURUSD", 1.1000)
    adapter.place_order(OrderRequest("o1", "EURUSD", Direction.long, size=10_000, stop_loss=1.0950, take_profit=1.1100))

    starting_equity = adapter.get_equity()
    fills = adapter.check_stops("EURUSD", low=1.0990, high=1.1110)
    assert len(fills) == 1
    assert fills[0].filled_price == 1.1100
    assert adapter.get_equity() > starting_equity


def test_short_position_pnl_direction():
    adapter = PaperAdapter(starting_balance=10_000, spread_pct=0, slippage_pct=0)
    adapter.update_price("EURUSD", 1.1000)
    adapter.place_order(OrderRequest("o1", "EURUSD", Direction.short, size=10_000, stop_loss=1.1050, take_profit=1.0900))

    fills = adapter.check_stops("EURUSD", low=1.0890, high=1.1010)
    assert fills[0].filled_price == 1.0900  # take profit for a short as price falls
    assert adapter.balance > 10_000  # short profits when price drops
