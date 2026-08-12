"""Instrument contract specifications — the single source of truth for turning a
stop distance into a position size.

Why this module exists
----------------------
"Risk 1% of equity, size from the stop distance" is universal. The arithmetic that
converts a stop distance into a position is NOT:

    size = risk_amount / stop_distance

is only correct when one unit of size gains $1 per 1.00 of price movement, and
when any fractional size is tradeable. Neither holds in general:

* An MES contract gains **$5** per index point, so a 10-point stop risks $50 per
  contract, not $10. The naive formula returns 5x too many contracts.
* Brokers accept discrete sizes. 20,437.3 units of EURUSD is not an order; the
  size must floor to the instrument's step (0.01 lot = 1,000 units).
* MT5 `order_send` takes **volume in lots**, not units. Passing 20,000 where 0.2
  was meant is a 100,000x position.

Every one of those is a silent error — the trade is placed, it just isn't the size
the risk manager intended. So the specs live here as data, are used by the risk
manager, the backtester, the journal and the execution adapters alike, and are
covered by tests.

The size convention
-------------------
`size` throughout this codebase is expressed in **units**, where:

    money moved = |price movement| x size x point_value

`point_value` is 1.0 for retail spot instruments (a unit of base currency, an
ounce of gold, a coin, a share) and is the contract multiplier for futures.
Broker "lots" are a separate concept: `contract_size` gives units per lot, and
only the execution adapters that speak in lots convert.

Sources
-------
Contract multipliers for exchange-listed futures are exchange-defined and stable.
Retail spot/CFD increments and lot sizes are **broker-specific** — the values here
are the common retail defaults and should be verified against the broker's own
contract specification before trading real money. Margin requirements are
deliberately absent: they change, they differ per broker, and guessing them in a
risk gate would be worse than not having them.
"""

from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from app.db.models import AssetClass

# Stop distances arrive as binary-float subtractions: abs(1.1000 - 1.0950) is
# 0.005000000000000004, which makes a 20,000-unit position compute as
# 19999.999999999985. Flooring that to a 1,000-unit step would silently drop a
# whole step (5% of the position). Differences below this fraction of one step are
# float noise, not a real shortfall, so the step count is snapped before flooring.
_STEP_EPSILON = Decimal("1e-9")


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    symbol: str
    asset_class: AssetClass
    unit: str
    point_value: float = 1.0     # money per 1.00 of price movement, per unit
    min_size: float = 0.01       # smallest order the broker accepts, in units
    size_step: float = 0.01      # size must be a whole multiple of this
    contract_size: float = 1.0   # units per broker "lot" (for lot-based APIs)
    is_class_default: bool = False

    @property
    def min_risk(self) -> float:
        """Money risked by the smallest possible position at a 1.00 stop distance.
        Multiply by the real stop distance to learn whether an account can afford
        this instrument at all."""
        return self.min_size * self.point_value


def _fx(symbol: str) -> InstrumentSpec:
    # Unit = 1 unit of the base currency. 1 standard lot = 100,000 units;
    # 0.01 lot (a "micro lot") = 1,000 units is the usual retail minimum.
    return InstrumentSpec(symbol, AssetClass.forex, "unit", 1.0, 1_000.0, 1_000.0, 100_000.0)


def _index_cfd(symbol: str) -> InstrumentSpec:
    # Retail index CFDs are quoted so 1 index point = 1 unit of currency per unit
    # of size. Minimum size is broker-specific; 0.1 is a common floor.
    return InstrumentSpec(symbol, AssetClass.indices, "contract", 1.0, 0.1, 0.1, 1.0)


def _crypto(symbol: str, asset_class: AssetClass = AssetClass.crypto_major) -> InstrumentSpec:
    return InstrumentSpec(symbol, asset_class, "coin", 1.0, 0.0001, 0.00001, 1.0)


INSTRUMENTS: dict[str, InstrumentSpec] = {
    # ---- Forex majors (correlation group usd_majors) ----
    **{s: _fx(s) for s in
       ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY")},

    # ---- Spot metals ----
    # 1 lot of gold = 100 oz, so 0.01 lot = 1 oz.
    "XAUUSD": InstrumentSpec("XAUUSD", AssetClass.metals, "ounce", 1.0, 1.0, 1.0, 100.0),
    # 1 lot of silver = 5,000 oz, so the 0.01-lot minimum is *50 ounces*. This is
    # why silver is unaffordable on a small account: the increment, not the price.
    "XAGUSD": InstrumentSpec("XAGUSD", AssetClass.metals, "ounce", 1.0, 50.0, 50.0, 5_000.0),

    # ---- Index CFDs (correlation group us_indices) ----
    **{s: _index_cfd(s) for s in ("US30", "NAS100", "SPX500", "US2000")},

    # ---- Crypto majors ----
    **{s: _crypto(s) for s in ("BTCUSD", "ETHUSD", "BTCUSDT", "ETHUSDT")},

    # ---- Exchange-listed futures: point_value is NOT 1 ----
    # These are the cases the naive formula gets wrong by the multiplier.
    "MES": InstrumentSpec("MES", AssetClass.indices, "contract", 5.0, 1.0, 1.0, 1.0),
    "ES": InstrumentSpec("ES", AssetClass.indices, "contract", 50.0, 1.0, 1.0, 1.0),
    "MNQ": InstrumentSpec("MNQ", AssetClass.indices, "contract", 2.0, 1.0, 1.0, 1.0),
    "NQ": InstrumentSpec("NQ", AssetClass.indices, "contract", 20.0, 1.0, 1.0, 1.0),
    "MYM": InstrumentSpec("MYM", AssetClass.indices, "contract", 0.5, 1.0, 1.0, 1.0),
    "MCL": InstrumentSpec("MCL", AssetClass.commodities, "contract", 100.0, 1.0, 1.0, 1.0),
    "CL": InstrumentSpec("CL", AssetClass.commodities, "contract", 1_000.0, 1.0, 1.0, 1.0),
    "MGC": InstrumentSpec("MGC", AssetClass.commodities, "contract", 10.0, 1.0, 1.0, 1.0),
    "GC": InstrumentSpec("GC", AssetClass.commodities, "contract", 100.0, 1.0, 1.0, 1.0),
}


# Long-tail symbols must still be sizeable — a meme-coin bucket cannot be a fixed
# enumeration. These defaults are per asset class, conservative, and flagged via
# `is_class_default` so callers can log that an exact spec was not found.
CLASS_DEFAULTS: dict[AssetClass, InstrumentSpec] = {
    AssetClass.forex: InstrumentSpec(
        "*forex", AssetClass.forex, "unit", 1.0, 1_000.0, 1_000.0, 100_000.0, is_class_default=True),
    AssetClass.indices: InstrumentSpec(
        "*indices", AssetClass.indices, "contract", 1.0, 0.1, 0.1, 1.0, is_class_default=True),
    AssetClass.metals: InstrumentSpec(
        "*metals", AssetClass.metals, "unit", 1.0, 0.01, 0.01, 1.0, is_class_default=True),
    AssetClass.commodities: InstrumentSpec(
        "*commodities", AssetClass.commodities, "unit", 1.0, 0.01, 0.01, 1.0, is_class_default=True),
    AssetClass.crypto_major: InstrumentSpec(
        "*crypto_major", AssetClass.crypto_major, "coin", 1.0, 0.0001, 0.00001, 1.0, is_class_default=True),
    # Meme tokens are frequently priced in fractions of a cent, so positions run to
    # millions of tokens and a whole-token step is the right granularity.
    AssetClass.crypto_meme: InstrumentSpec(
        "*crypto_meme", AssetClass.crypto_meme, "token", 1.0, 1.0, 1.0, 1.0, is_class_default=True),
    AssetClass.stocks: InstrumentSpec(
        "*stocks", AssetClass.stocks, "share", 1.0, 1.0, 1.0, 1.0, is_class_default=True),
}


class UnknownInstrument(LookupError):
    """Raised where guessing a spec would be unsafe (live execution adapters)."""


def spec_for(instrument: str, asset_class: AssetClass | None = None) -> InstrumentSpec | None:
    """Exact symbol match, else the asset-class default, else None.

    Pass `asset_class` wherever a sensible fallback is better than refusing to
    size (the risk manager). Omit it where a wrong guess would place a real order
    at the wrong size (execution adapters) and handle None by failing closed.
    """
    exact = INSTRUMENTS.get(instrument.upper())
    if exact is not None:
        return exact
    if asset_class is not None:
        return CLASS_DEFAULTS.get(asset_class)
    return None


def require_spec(instrument: str, asset_class: AssetClass | None = None) -> InstrumentSpec:
    spec = spec_for(instrument, asset_class)
    if spec is None:
        raise UnknownInstrument(
            f"No contract specification for {instrument!r}. Add it to "
            f"app/risk/instruments.py rather than letting a size be guessed."
        )
    return spec


def point_value_for(instrument: str, asset_class: AssetClass | None = None) -> float:
    """Money per 1.00 of price movement per unit of size. Defaults to 1.0 for
    unknown symbols, which is correct for every retail spot instrument and keeps
    P&L math unchanged for anything not in the registry."""
    spec = spec_for(instrument, asset_class)
    return spec.point_value if spec is not None else 1.0


def round_size(raw_size: float, spec: InstrumentSpec) -> float:
    """Floor a size to a tradeable multiple of `size_step`.

    Returns 0.0 when the result is below `min_size` — meaning the instrument does
    not fit the risk budget at this stop distance. Callers must treat 0.0 as
    "no trade", never round it up: one unit of an instrument whose minimum risks
    more than the budget is precisely the trade the risk gate exists to stop.
    """
    if raw_size <= 0 or spec.size_step <= 0:
        return 0.0
    step = Decimal(str(spec.size_step))
    # Snap away float noise (see _STEP_EPSILON) before flooring, so a size that is
    # a hair under a step boundary is not rounded down by a full step.
    steps = (Decimal(str(raw_size)) / step).quantize(_STEP_EPSILON, rounding=ROUND_HALF_UP)
    size = float(steps.to_integral_value(rounding=ROUND_FLOOR) * step)
    if size < spec.min_size:
        return 0.0
    return size


def size_for_risk(risk_amount: float, stop_distance: float, spec: InstrumentSpec) -> float:
    """The whole point of this module: risk budget -> tradeable position size."""
    if stop_distance <= 0 or spec.point_value <= 0:
        return 0.0
    return round_size(risk_amount / (stop_distance * spec.point_value), spec)


def risk_for_size(stop_distance: float, size: float, spec: InstrumentSpec) -> float:
    """Money actually at risk for a given size. After flooring to the step this is
    lower than the requested budget, and it is what portfolio/correlation caps
    must be summed on — otherwise exposure is overstated."""
    return abs(stop_distance) * size * spec.point_value


def units_to_lots(size: float, spec: InstrumentSpec) -> float:
    """Convert internal units to broker lots for lot-based APIs (e.g. MT5 volume)."""
    if spec.contract_size <= 0:
        return size
    return size / spec.contract_size


def lots_to_units(lots: float, spec: InstrumentSpec) -> float:
    return lots * spec.contract_size
