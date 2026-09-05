
import pytest

from pmbot.execution.engine import ExecutionEngine
from pmbot.execution.paper import PaperExecutor
from pmbot.execution.registry import OrderRegistry, OrderState
from pmbot.strategies.base import Signal
from tests.helpers import make_book, make_ctx, make_market


def paper(fee_rate=0.05):
    registry = OrderRegistry()
    return registry, PaperExecutor(registry, lambda token_id: fee_rate)


# ---- registry -------------------------------------------------------------

def test_order_state_machine():
    registry = OrderRegistry()
    order = registry.new_order("s1_maker", "tok", "BUY", 0.50, 100)
    assert order.status is OrderState.PENDING
    registry.ack(order.client_id, "ex-1")
    assert order.status is OrderState.OPEN
    registry.fill("ex-1", 0.50, 40)
    assert order.status is OrderState.PARTIAL and order.remaining == 60
    registry.fill(order.client_id, 0.50, 60)
    assert order.status is OrderState.FILLED and not order.is_live


def test_client_id_is_minted_before_signing_for_idempotency():
    registry = OrderRegistry()
    a = registry.new_order("s1_maker", "tok", "BUY", 0.5, 10)
    b = registry.new_order("s1_maker", "tok", "BUY", 0.5, 10)
    assert a.client_id != b.client_id and a.client_id


def test_cancel_is_terminal_and_idempotent():
    registry = OrderRegistry()
    order = registry.new_order("s1_maker", "tok", "BUY", 0.5, 10)
    registry.ack(order.client_id, "ex-1")
    registry.cancel("ex-1")
    registry.cancel("ex-1")
    assert order.status is OrderState.CANCELLED


def test_reconcile_adopts_orphans_and_corrects_fills():
    registry = OrderRegistry()
    known = registry.new_order("s1_maker", "tok", "BUY", 0.50, 100)
    registry.ack(known.client_id, "ex-1")
    divergences = registry.reconcile([
        {"id": "ex-1", "size_matched": 25},
        {"id": "ex-2", "asset_id": "tok", "side": "BUY", "price": 0.4, "original_size": 50},
    ])
    assert known.filled == 25 and known.status is OrderState.PARTIAL
    assert any("orphan" in d for d in divergences)
    assert registry.get("ex-2") is not None


def test_reconcile_marks_locally_live_orders_that_vanished():
    registry = OrderRegistry()
    order = registry.new_order("s1_maker", "tok", "BUY", 0.50, 100)
    registry.ack(order.client_id, "ex-1")
    divergences = registry.reconcile([])
    assert order.status is OrderState.CANCELLED
    assert any("absent remotely" in d for d in divergences)


def test_exchange_wide_cancel_is_a_normal_state():
    registry = OrderRegistry()
    for _ in range(3):
        order = registry.new_order("s1_maker", "tok", "BUY", 0.50, 100)
        registry.ack(order.client_id, f"ex-{order.client_id[:4]}")
    assert registry.mark_all_cancelled() == 3
    assert registry.open_count() == 0


# ---- paper fills ----------------------------------------------------------

async def test_maker_order_does_not_fill_at_its_own_price():
    registry, executor = paper()
    book = make_book(last_trade=0.50)
    order = registry.new_order("s1_maker", "tok", "BUY", 0.50, 100)
    await executor.post(order, book)
    book.last_trade_price = 0.50  # printed *at* our price: the queue ahead fills
    assert executor.on_book(book) == []


async def test_maker_order_fills_when_the_book_trades_through():
    registry, executor = paper()
    book = make_book(last_trade=0.50)
    order = registry.new_order("s1_maker", "tok", "BUY", 0.50, 100)
    await executor.post(order, book)
    book.last_trade_price = 0.49
    fills = executor.on_book(book)
    assert len(fills) == 1
    assert fills[0].is_maker and fills[0].fee == 0.0  # maker pays nothing


async def test_fok_taker_pays_the_fee_and_a_tick_of_slippage():
    registry, executor = paper(fee_rate=0.04)
    book = make_book(asks=((0.51, 500),))
    order = registry.new_order("s2_arb", "tok", "BUY", 0.51, 100, order_type="FOK")
    await executor.post(order, book)
    fill = executor.fills[0]
    assert fill.price == 0.52  # 0.51 + one tick of adverse slippage
    assert fill.fee == pytest.approx(100 * 0.04 * 0.52 * 0.48)
    assert not fill.is_maker


async def test_fok_is_rejected_when_the_book_cannot_fill_it_whole():
    registry, executor = paper()
    book = make_book(asks=((0.51, 10),))
    order = registry.new_order("s2_arb", "tok", "BUY", 0.51, 100, order_type="FOK")
    await executor.post(order, book)
    assert order.status is OrderState.REJECTED and not executor.fills


# ---- engine ---------------------------------------------------------------

async def test_fill_updates_position_and_cash():
    registry, executor = paper()
    book = make_book(last_trade=0.50)
    ctx = make_ctx(books={"tok": book}, markets={"tok": make_market()}, free_usdc=1000)
    engine = ExecutionEngine(executor, registry, ctx)
    signal = Signal(strategy="s1_maker", token_id="tok", side="BUY", price=0.50, size=100)
    order = await engine.submit(signal, 100)
    await engine.handle_fill(order.client_id, 0.50, 100, 0.0, True, ctx.now)
    assert ctx.portfolio.free_usdc == 950
    assert ctx.portfolio.position("tok").qty == 100


async def test_partial_arb_group_is_unwound_not_held():
    registry, executor = paper()
    yes = make_book(token_id="yes", bids=((0.44, 500),), asks=((0.45, 500),))
    no = make_book(token_id="no", bids=((0.49, 500),), asks=((0.50, 5),))
    ctx = make_ctx(books={"yes": yes, "no": no},
                   markets={"yes": make_market(token_id="yes"), "no": make_market(token_id="no")})
    engine = ExecutionEngine(executor, registry, ctx, unwind_timeout_s=0)
    legs = [
        Signal(strategy="s2_arb", token_id="yes", side="BUY", price=0.45, size=100,
               order_type="FOK", group_id="g1"),
        Signal(strategy="s2_arb", token_id="no", side="BUY", price=0.50, size=100,
               order_type="FOK", group_id="g1"),
    ]
    await engine.submit_group(legs, [100, 100])
    unwinds = [o for o in registry.orders.values() if o.group_id == "g1:unwind"]
    # The 'no' leg could not fill 100, so the filled 'yes' leg is sold back out.
    assert len(unwinds) == 1 and unwinds[0].token_id == "yes" and unwinds[0].side == "SELL"


async def test_shutdown_cancels_every_resting_order():
    registry, executor = paper()
    book = make_book()
    ctx = make_ctx(books={"tok": book}, markets={"tok": make_market()})
    engine = ExecutionEngine(executor, registry, ctx)
    for _ in range(3):
        await engine.submit(
            Signal(strategy="s1_maker", token_id="tok", side="BUY", price=0.49, size=10), 10
        )
    assert await engine.shutdown() == 3
    assert registry.open_count() == 0
