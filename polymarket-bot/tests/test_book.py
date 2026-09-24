import time

from pmbot.data.book import OrderBook
from tests.helpers import make_book


def test_delta_before_snapshot_is_ignored():
    # A book we never snapshotted would look live while missing every level
    # we never saw. Refuse the delta and demand a re-snapshot instead.
    book = OrderBook(token_id="tok")
    assert book.needs_resnapshot
    book.apply_price_change([{"side": "BUY", "price": 0.4, "size": 100}])
    assert book.bids == {}


def test_delta_applies_and_zero_size_removes_a_level():
    book = make_book(bids=((0.49, 500), (0.48, 100)), asks=((0.51, 500),))
    book.apply_price_change([
        {"side": "BUY", "price": 0.50, "size": 200},
        {"side": "BUY", "price": 0.48, "size": 0},
    ])
    assert book.best_bid == 0.50
    assert 0.48 not in book.bids


def test_disconnect_marks_stale_regardless_of_age():
    book = make_book(ts=time.time())
    assert not book.is_stale(1500)
    book.mark_stale()
    assert book.is_stale(1500)


def test_age_drives_staleness():
    book = make_book(ts=time.time() - 5)
    assert book.is_stale(1500)
    assert book.age_ms() > 4900


def test_depth_usd_only_counts_levels_near_touch():
    book = make_book(bids=((0.49, 100), (0.40, 10_000)))
    # The 0.40 level is 9c away: it is not depth you can trade against.
    assert book.depth_usd("BUY", within_cents=2) == 0.49 * 100


def test_sweep_cost_averages_across_levels():
    book = make_book(asks=((0.51, 100), (0.52, 100)))
    avg, filled = book.sweep_cost("SELL", 150)
    assert filled == 150
    assert abs(avg - (0.51 * 100 + 0.52 * 50) / 150) < 1e-9


def test_sweep_cost_reports_partial_fill():
    book = make_book(asks=((0.51, 10),))
    avg, filled = book.sweep_cost("SELL", 100)
    assert filled == 10 and avg == 0.51


def test_round_to_tick():
    book = make_book(tick=0.01)
    assert book.round_to_tick(0.4732) == 0.47
    fine = make_book(tick=0.001)
    assert fine.round_to_tick(0.47349) == 0.473
