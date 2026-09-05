import math

from pmbot.fees import (
    DEFAULT_FEE_RATES,
    FeeTable,
    edge_per_day,
    fee_per_share,
    is_tradeable,
    taker_fee,
)


def test_taker_fee_matches_published_formula():
    # shares x rate x price x (1-price)
    assert taker_fee(100, 0.5, 0.04) == 100 * 0.04 * 0.5 * 0.5


def test_fee_is_near_zero_at_the_extremes():
    # This is the whole reason S4 can clear costs at 2-6c.
    assert fee_per_share(0.98, 0.05) < 0.001
    assert fee_per_share(0.50, 0.05) > 10 * fee_per_share(0.98, 0.05)


def test_maker_pays_nothing_so_a_thin_edge_still_clears():
    assert is_tradeable(0.006, 0.5, 0.07, is_maker=True, slippage=0.005)
    assert not is_tradeable(0.006, 0.5, 0.07, is_maker=False, slippage=0.005)


def test_edge_per_day_penalises_capital_lockup():
    # 3c over 45 days is worse than 1c over 2 days.
    assert edge_per_day(0.03, 45 * 24) < edge_per_day(0.01, 2 * 24)


def test_worst_known_tag_wins():
    table = FeeTable()
    # A crypto market also tagged geopolitics is charged as crypto.
    assert table.rate_for_tags(["geopolitics", "crypto"]) == DEFAULT_FEE_RATES["crypto"]
    assert table.rate_for_tags(["unknown-tag"]) == DEFAULT_FEE_RATES["other"]


def test_rate_change_is_reported_and_alerted():
    seen = []
    table = FeeTable(on_change=lambda cat, old, new: seen.append((cat, old, new)))
    changed = table.update({"crypto": 0.09, "sports": 0.05})
    assert changed == ["crypto"]  # sports was already 0.05
    assert seen == [("crypto", 0.07, 0.09)]
    assert table.rate_for("crypto") == 0.09


def test_exponent_is_honoured():
    assert math.isclose(taker_fee(1, 0.5, 0.04, exponent=2.0), 0.04 * 0.25 ** 2)
