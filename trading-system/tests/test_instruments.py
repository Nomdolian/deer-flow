from decimal import Decimal

import pytest

from app.db.models import AssetClass
from app.risk.instruments import (
    CLASS_DEFAULTS,
    INSTRUMENTS,
    UnknownInstrument,
    lots_to_units,
    min_account_for,
    point_value_for,
    profile_for,
    require_spec,
    risk_for_size,
    round_size,
    size_for_risk,
    spec_for,
    units_to_lots,
)
from app.risk.instruments import UnknownBrokerProfile


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

def test_exact_symbol_beats_class_default():
    spec = spec_for("XAGUSD", AssetClass.metals)
    assert spec.symbol == "XAGUSD"
    assert spec.is_class_default is False
    assert spec.min_size == 50.0


def test_symbol_lookup_is_case_insensitive():
    assert spec_for("eurusd").symbol == "EURUSD"


def test_unlisted_symbol_uses_class_default():
    spec = spec_for("WIFUSD", AssetClass.crypto_meme)
    assert spec.is_class_default is True
    assert spec.asset_class == AssetClass.crypto_meme


def test_unlisted_symbol_without_class_is_unresolvable():
    """Execution adapters must be able to fail closed rather than guess."""
    assert spec_for("WIFUSD") is None
    with pytest.raises(UnknownInstrument):
        require_spec("WIFUSD")


def test_every_asset_class_has_a_default():
    for asset_class in AssetClass:
        assert asset_class in CLASS_DEFAULTS, asset_class


# --------------------------------------------------------------------------
# Rounding
# --------------------------------------------------------------------------

def test_round_size_floors_to_step():
    spec = spec_for("EURUSD")
    assert round_size(20_437.3, spec) == 20_000.0
    assert round_size(999.0, spec) == 0.0          # below the 1,000-unit minimum
    assert round_size(-5.0, spec) == 0.0


def test_float_noise_does_not_cost_a_whole_step():
    """Regression: abs(1.1000 - 1.0950) is 0.005000000000000004, so a 20,000-unit
    position computes as 19999.999999999985. Naive flooring returned 19,000 —
    silently 5% smaller than intended."""
    spec = spec_for("EURUSD")
    stop_distance = abs(1.1000 - 1.0950)
    assert round_size(100.0 / stop_distance, spec) == 20_000.0


def test_genuine_shortfall_still_floors_down():
    """The epsilon must not mask a real gap: 19,500 is meaningfully short of 20,000."""
    spec = spec_for("EURUSD")
    assert round_size(19_500.0, spec) == 19_000.0


def test_size_is_always_a_multiple_of_the_step():
    # Decimal, not float division: a crypto step of 0.00001 against a large size
    # gives a step count near 1e11, where float division alone is imprecise
    # enough to fail a correct result.
    for symbol, spec in INSTRUMENTS.items():
        size = size_for_risk(1_000_000.0, 1.0, spec)
        assert size > 0, symbol
        remainder = Decimal(str(size)) % Decimal(str(spec.size_step))
        assert remainder == 0, (symbol, size, spec.size_step)


# --------------------------------------------------------------------------
# Contract multipliers
# --------------------------------------------------------------------------

def test_point_value_scales_size_down():
    """10-point stop on MES ($5/pt) risks $50 per contract, so a $500 budget buys
    10 contracts — not the 50 the equity formula would return. Futures live on the
    generic profile; the live Exness account cannot trade them."""
    spec = spec_for("MES", broker="generic")
    assert spec.point_value == 5.0
    assert size_for_risk(500.0, 10.0, spec) == 10.0
    assert risk_for_size(10.0, 10.0, spec) == 500.0


def test_point_value_defaults_to_one_for_unknown_symbols():
    """Keeps P&L math unchanged for anything not in the registry."""
    assert point_value_for("SOMETHING_NEW") == 1.0
    assert point_value_for("EURUSD") == 1.0
    assert point_value_for("MCL", broker="generic") == 100.0


def test_spot_instruments_all_have_unit_point_value():
    for symbol in ("EURUSD", "XAUUSD", "XAGUSD", "BTCUSD", "US30"):
        assert spec_for(symbol).point_value == 1.0, symbol


# --------------------------------------------------------------------------
# Broker profiles — specs are per broker, and the differences change conclusions
# --------------------------------------------------------------------------

def test_default_profile_is_the_live_broker():
    assert profile_for().key == "exness_standard"
    assert profile_for("exness").key == "exness_standard"   # alias
    assert profile_for("EXNESS_CENT").key == "exness_cent"


def test_unknown_profile_is_rejected():
    with pytest.raises(UnknownBrokerProfile):
        profile_for("some_broker_we_never_configured")


def test_exness_does_not_offer_exchange_futures():
    """A CFD broker has no CME contracts. Asking for one must fail closed rather
    than fall back to an asset-class default and size an untradeable contract."""
    assert spec_for("MES", AssetClass.indices, broker="exness") is not None  # class default
    assert spec_for("MES", broker="exness") is None                          # no exact spec
    assert spec_for("MES", broker="generic").point_value == 5.0


def test_exness_index_symbols_differ_from_generic_names():
    """Exness names the S&P 500 US500 and the Nasdaq USTEC. Sizing off another
    broker's ticker means sizing off a spec that is not this account's."""
    assert spec_for("US500", broker="exness") is not None
    assert spec_for("USTEC", broker="exness") is not None
    assert spec_for("SPX500", broker="exness") is None
    assert spec_for("NAS100", broker="exness") is None


def test_exness_crypto_is_a_cfd_not_spot_sizing():
    """The correction that matters most for a small account: a spot exchange sizes
    BTC to 8 decimals, so any budget fits. Exness's 0.01-lot floor is 0.01 BTC."""
    exness = spec_for("BTCUSD", broker="exness")
    spot = spec_for("BTCUSD", broker="generic")
    assert exness.min_size == 0.01
    assert spot.min_size == 0.0001
    assert exness.min_size / spot.min_size == 100.0

    stop = 1236.0
    # $0.50 of budget buys a position on the exchange but not at the broker.
    assert size_for_risk(0.50, stop, spot) > 0
    assert size_for_risk(0.50, stop, exness) == 0.0
    # Risk carried by the smallest possible Exness position:
    assert abs(risk_for_size(stop, exness.min_size, exness) - 12.36) < 0.01


def test_cent_account_lot_is_a_hundred_times_finer():
    """A cent lot is 1,000 units, so 0.01 lot is 10 units rather than 1,000. This is
    what makes forex reachable on a small account."""
    standard = spec_for("EURUSD", broker="exness_standard")
    cent = spec_for("EURUSD", broker="exness_cent")
    assert standard.contract_size == 100_000.0
    assert cent.contract_size == 1_000.0
    assert standard.min_size == 1_000.0
    assert cent.min_size == 10.0

    stop = 0.0020            # 20 pips
    budget = 0.50            # 1% of a $50 account
    assert size_for_risk(budget, stop, standard) == 0.0     # cannot fit
    assert size_for_risk(budget, stop, cent) > 0            # fits comfortably


def test_min_account_gives_the_honest_affordability_threshold():
    """Below this equity the instrument cannot be traded without breaking the risk
    rule, and no amount of leverage changes it."""
    cent = spec_for("EURUSD", broker="exness_cent")
    standard = spec_for("EURUSD", broker="exness_standard")
    btc = spec_for("BTCUSD", broker="exness")

    assert min_account_for(0.0020, cent, 0.01) == pytest.approx(2.0, abs=0.01)
    assert min_account_for(0.0020, standard, 0.01) == pytest.approx(200.0, abs=0.01)
    assert min_account_for(1236.0, btc, 0.01) == pytest.approx(1236.0, abs=1.0)


def test_all_broker_profile_specs_are_marked_unverified():
    """Broker specs are recorded from documentation, not read from the account.
    They stay unverified until scripts/verify_broker_specs.py reconciles them
    against MT5 symbol_info() — a wrong contract_size silently mis-sizes."""
    for key in ("exness_standard", "exness_cent"):
        for symbol, spec in profile_for(key).specs.items():
            assert spec.verified is False, symbol


# --------------------------------------------------------------------------
# Lot conversion
# --------------------------------------------------------------------------

def test_units_and_lots_round_trip():
    spec = spec_for("EURUSD")
    assert units_to_lots(20_000.0, spec) == 0.2
    assert lots_to_units(0.2, spec) == 20_000.0


def test_lot_conversion_per_asset_class():
    assert units_to_lots(20_000.0, spec_for("EURUSD")) == 0.2      # 100k units/lot
    assert units_to_lots(1.0, spec_for("XAUUSD")) == 0.01          # 100 oz/lot
    assert units_to_lots(50.0, spec_for("XAGUSD")) == 0.01         # 5,000 oz/lot
    assert units_to_lots(3.0, spec_for("MES", broker="generic")) == 3.0  # contracts are lots


# --------------------------------------------------------------------------
# The invariant
# --------------------------------------------------------------------------

def test_risk_never_exceeds_budget_across_registry_and_geometries():
    budget = 1_000.0
    geometries = [
        (1.1000, 1.0950), (155.20, 154.90), (4408.55, 4393.55), (66.11, 65.61),
        (63736.0, 62500.0), (5000.0, 4990.0), (81.96, 81.46), (0.31, 0.30),
        (46000.0, 45900.0),
    ]
    specs = list(INSTRUMENTS.values()) + list(CLASS_DEFAULTS.values())
    for spec in specs:
        for entry, stop in geometries:
            stop_distance = abs(entry - stop)
            size = size_for_risk(budget, stop_distance, spec)
            risk = risk_for_size(stop_distance, size, spec)
            assert risk <= budget + 1e-6, (spec.symbol, entry, stop, risk)
            if size == 0.0:
                assert risk == 0.0


def test_zero_and_degenerate_inputs_are_safe():
    spec = spec_for("EURUSD")
    assert size_for_risk(100.0, 0.0, spec) == 0.0      # no stop distance
    assert size_for_risk(0.0, 0.005, spec) == 0.0      # no budget
    assert size_for_risk(-100.0, 0.005, spec) == 0.0   # negative budget
