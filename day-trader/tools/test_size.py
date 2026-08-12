#!/usr/bin/env python3
"""
Tests for the multi-asset position sizer.

Run:  python3 -m pytest tools/test_size.py -q
      python3 tools/test_size.py          (no pytest needed)

The invariant that matters most is the last one: across every instrument in the
registry, the computed position can never risk more than the risk budget. That is
the single promise the whole system rests on, so it is asserted exhaustively
rather than by example.
"""

from decimal import Decimal

from size import REGISTRY, floor_to_increment, resolve, size_position

TOL = 1e-9


# --------------------------------------------------------------------------
# Regression: the documented stock example must never drift
# --------------------------------------------------------------------------

def test_stock_example_from_the_docs():
    """Entry 10.50 / stop 10.20 / target 11.10 on $50 at 1% -> 1 share, $0.30 risk."""
    r = size_position(50, 1.0, 10.50, 10.20, target=11.10)
    assert r["units"] == 1
    assert abs(r["actual_risk"] - 0.30) < 1e-6
    assert abs(r["actual_risk_pct"] - 0.60) < 1e-6
    assert abs(r["notional"] - 10.50) < 1e-6
    assert abs(r["profit_at_target"] - 0.60) < 1e-6
    assert r["binding_constraint"] == "risk budget"
    assert r["warnings"] == [] or all("widening" in w or "rounding" in w for w in r["warnings"])


def test_rr_exactly_two_does_not_trip_the_skip_warning():
    """0.60/0.30 evaluates to 1.9999999999999993 in binary float. The 1e-9
    tolerance exists so a textbook 1:2 trade is not rejected by a rounding
    artifact."""
    r = size_position(50, 1.0, 10.50, 10.20, target=11.10)
    assert abs(r["rr"] - 2.0) < 1e-9
    assert not any("SKIP THIS TRADE" in w for w in r["warnings"])


def test_rr_below_two_is_flagged():
    r = size_position(50, 1.0, 10.50, 10.20, target=10.80)
    assert any("SKIP THIS TRADE" in w for w in r["warnings"])


# --------------------------------------------------------------------------
# Increment flooring
# --------------------------------------------------------------------------

def test_floor_to_increment():
    assert floor_to_increment(3.7, Decimal("1")) == Decimal("3")
    assert floor_to_increment(1750, Decimal("1000")) == Decimal("1000")
    assert floor_to_increment(999, Decimal("1000")) == Decimal("0")
    assert floor_to_increment(0.000404, Decimal("0.00000001")) == Decimal("0.00040400")
    assert floor_to_increment(-5, Decimal("1")) == Decimal("0")


def test_crypto_uses_the_entire_risk_budget():
    """Native fractional sizing is the reason crypto wastes no budget — this is
    the quantitative claim the docs make, so it is pinned here."""
    r = size_position(50, 1.0, 63736.0, 62500.0, instrument="crypto-spot")
    assert r["budget_used_pct"] > 99.9
    assert abs(r["actual_risk"] - 0.50) < 0.001


def test_whole_share_rounding_wastes_budget():
    """The same 1% budget on a whole-share instrument cannot be fully used."""
    r = size_position(50, 1.0, 10.50, 10.20)
    assert 55 < r["budget_used_pct"] < 65


# --------------------------------------------------------------------------
# Contract multipliers
# --------------------------------------------------------------------------

def test_point_value_scales_risk():
    """A 10-point stop on MES ($5/pt) risks $50, not $10. Getting this wrong is
    the exact bug an equity-only formula produces on a futures contract."""
    r = size_position(5000, 1.0, 6800.0, 6790.0, instrument="mes")
    assert abs(r["risk_per_unit"] - 50.0) < 1e-6
    assert r["units"] == 1
    assert abs(r["actual_risk"] - 50.0) < 1e-6


def test_equity_formula_would_be_wrong_for_mes():
    """Guards against regressing to size = budget / stop_distance."""
    naive = (5000 * 0.01) / abs(6800.0 - 6790.0)   # = 5 contracts
    r = size_position(5000, 1.0, 6800.0, 6790.0, instrument="mes")
    assert naive == 5
    assert r["units"] == 1, "point value must divide the size down by 5x"


def test_silver_increment_blocks_the_trade_not_the_price():
    """XAGUSD min size is 50 oz, so even a 50-cent stop risks $25."""
    r = size_position(50, 1.0, 66.11, 65.61, instrument="xagusd")
    assert r["units"] == 0
    assert any("POSITION SIZE IS ZERO" in w for w in r["warnings"])


def test_zero_size_never_rounds_up():
    for key in ("xauusd", "xagusd", "mes", "es", "cl", "ng"):
        r = size_position(50, 1.0, 100.0, 95.0, instrument=key)
        assert r["units"] == 0, key
        assert r["actual_risk"] == 0.0, key


# --------------------------------------------------------------------------
# Forex quote conversion
# --------------------------------------------------------------------------

def test_forex_nano_fits_a_small_account():
    r = size_position(50, 1.0, 1.15399, 1.15199, instrument="forex-nano")
    assert r["units"] == 200
    assert abs(r["actual_risk"] - 0.40) < 0.01


def test_non_usd_quote_conversion_changes_risk():
    """USDJPY risk is in yen until converted. Without --quote-usd the figure is
    ~155x too large, which would silently undersize by the same factor."""
    unconverted = size_position(50, 1.0, 155.20, 154.90, instrument="forex-nano")
    converted = size_position(50, 1.0, 155.20, 154.90, instrument="forex-nano",
                              quote_usd=1 / 155.20)
    assert converted["risk_per_unit"] < unconverted["risk_per_unit"]
    assert converted["units"] > unconverted["units"]


def test_non_usd_quote_warning_fires():
    r = size_position(50, 1.0, 155.20, 154.90, instrument="forex-micro", symbol="USDJPY")
    assert any("not USD-quoted" in w for w in r["warnings"])


# --------------------------------------------------------------------------
# Capital / margin handling
# --------------------------------------------------------------------------

def test_unknown_futures_margin_skips_capital_check_and_warns():
    r = size_position(5000, 1.0, 6800.0, 6790.0, instrument="mes")
    assert r["capital_unknown"] is True
    assert r["units_by_capital"] is None
    assert any("capital check was SKIPPED" in w for w in r["warnings"])


def test_supplied_margin_binds_the_size():
    r = size_position(5000, 1.0, 6800.0, 6790.0, instrument="mes", margin_per_unit=40.0)
    assert r["capital_unknown"] is False
    assert r["units_by_capital"] is not None


def test_capital_pct_is_zero_not_none_at_zero_size():
    """Regression: 0.0 is falsy, and a truthiness guard here produced a None that
    crashed rendering."""
    r = size_position(50, 1.0, 4408.55, 4393.55, instrument="xauusd")
    assert r["units"] == 0
    assert r["capital_used"] == 0.0
    assert r["capital_pct_of_account"] == 0.0


def test_cash_account_capital_can_bind_below_risk_size():
    """A cheap stock with a tight stop: risk allows more shares than cash buys."""
    r = size_position(50, 3.0, 4.20, 4.15, instrument="stock")
    assert r["units_by_risk"] > r["units_by_capital"], "cash should be the tighter bound here"
    assert r["units"] == r["units_by_capital"]
    assert r["binding_constraint"] == "available capital"


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def test_rejects_degenerate_inputs():
    for kwargs in (
        dict(entry=10.0, stop=10.0),
        dict(entry=-1.0, stop=1.0),
        dict(entry=10.0, stop=0.0),
    ):
        try:
            size_position(50, 1.0, **kwargs)
        except ValueError:
            continue
        raise AssertionError(f"should have raised for {kwargs}")


def test_rejects_unknown_instrument():
    try:
        resolve("definitely-not-an-instrument")
    except ValueError as e:
        assert "Unknown instrument" in str(e)
    else:
        raise AssertionError("should have raised")


def test_aliases_resolve():
    assert resolve("gold").key == "xauusd"
    assert resolve("BTC").key == "crypto-spot"
    assert resolve("  Fx  ").key == "forex-micro"


def test_risk_ceiling_warning():
    r = size_position(50, 5.0, 10.50, 10.20)
    assert any("hard ceiling" in w for w in r["warnings"])


def test_short_direction_detected():
    r = size_position(50, 1.0, 63736.0, 64500.0, instrument="crypto-spot")
    assert r["direction"] == "SHORT"
    assert r["risk_per_unit"] > 0


# --------------------------------------------------------------------------
# The universal invariant
# --------------------------------------------------------------------------

def test_risk_never_exceeds_budget_for_any_instrument():
    """Exhaustive across the registry and a spread of price/stop geometries.
    If this ever fails, the system's core promise is broken."""
    for key in REGISTRY:
        for entry, stop in ((100.0, 99.0), (10.0, 9.5), (63736.0, 62500.0),
                            (1.154, 1.152), (4408.0, 4390.0), (66.11, 65.61),
                            (6800.0, 6790.0), (0.5, 0.48)):
            for risk_pct in (1.0, 3.0):
                r = size_position(50, risk_pct, entry, stop, instrument=key)
                assert r["actual_risk"] <= r["risk_budget"] + TOL, (
                    f"{key} entry={entry} stop={stop} risk%={risk_pct}: "
                    f"risked {r['actual_risk']} of {r['risk_budget']}")
                assert r["units"] >= 0


def test_size_is_always_a_whole_multiple_of_the_increment():
    for key, inst in REGISTRY.items():
        r = size_position(1_000_000, 1.0, 100.0, 99.0, instrument=key)
        assert r["units"] % inst.increment == 0, key


if __name__ == "__main__":
    import sys
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:                                  # noqa: BLE001
            failed += 1
            print(f"  FAIL  {name}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
