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
* Brokers accept discrete sizes only. On Exness Standard, EURUSD's minimum is
  0.01 lot = 1,000 units, so 20,437.3 units is not an order.
* MT5 `order_send` takes **volume in lots**, not units. Passing 20,000 where 0.2
  was meant is a 100,000x position.

Every one of those is a silent error — the trade is placed, it just isn't the size
the risk manager intended.

Specs are per BROKER, not universal
-----------------------------------
The same ticker has different minimums, steps and contract sizes at different
brokers, and the differences decide which asset classes a small account can reach
at all. Two examples that invert conclusions:

* **Crypto.** A spot exchange sizes BTC to 8 decimals, so any budget fits. Exness
  trades crypto as a CFD with a 0.01-lot minimum — 0.01 BTC, ~$637 notional, and
  ~$12 of risk on a $1,236 stop. Viable on a spot exchange at any size; needs a
  ~$1,200 account at Exness.
* **Forex.** Exness Standard's 0.01-lot minimum is 1,000 units (~$2 risk on a
  20-pip stop). A Standard **Cent** account's lot is 1,000 units, so 0.01 lot is
  **10 units** (~$0.02 risk) — a hundredfold finer, and the difference between
  forex being reachable or not on a small account.

So profiles are selected by `settings.broker` / `settings.account_type`.

The size convention
-------------------
`size` throughout this codebase is in **units**, where:

    money moved = |price movement| x size x point_value

`point_value` is 1.0 for spot/CFD instruments (a unit of base currency, an ounce of
gold, a coin, an index point) and the contract multiplier for exchange futures.
Broker "lots" are separate: `contract_size` gives units per lot, and only adapters
speaking lots convert.

Provenance — READ THIS BEFORE TRADING REAL MONEY
------------------------------------------------
Exchange futures multipliers are exchange-defined and stable. Everything else here
is a **broker default recorded from documentation, not read from your account**,
and brokers change specs, vary them by region, and vary them by account type. Each
spec carries `verified=False` until reconciled against the live account.

`scripts/verify_broker_specs.py` reads MT5's own `symbol_info()` and reports every
mismatch. Run it before trading. It is the only way to be certain, and a wrong
`contract_size` here produces a wrong position size that nothing else will catch.

Margins are deliberately absent: they change, they differ per broker and per
instrument, and guessing one inside a risk gate is worse than not having it.
"""

from dataclasses import dataclass, replace
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
    # Currency the price is quoted in. When this is not the account currency, a
    # price movement is NOT money: a 0.30 move on USDJPY is 0.30 JPY (~$0.0019),
    # not $0.30. Sizing without converting overstates risk ~155x on JPY pairs.
    quote_currency: str = "USD"
    is_class_default: bool = False
    # False until reconciled against the live account by verify_broker_specs.py.
    verified: bool = False

    @property
    def min_risk_per_point(self) -> float:
        """Money risked by the smallest possible position per 1.00 of stop
        distance. Multiply by the real stop distance to learn whether an account
        can afford this instrument at all."""
        return self.min_size * self.point_value


@dataclass(frozen=True, slots=True)
class BrokerProfile:
    key: str
    name: str
    specs: dict[str, InstrumentSpec]
    class_defaults: dict[AssetClass, InstrumentSpec]
    # True when the broker's order API takes volume in lots (MT4/MT5) rather than
    # in units. Drives whether adapters must convert.
    lot_based: bool = True
    notes: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Spec builders
# ---------------------------------------------------------------------------

def _fx(symbol: str, contract_size: float) -> InstrumentSpec:
    """Unit = 1 unit of the base currency. min/step are the broker's 0.01 lot.

    The quote currency is the second half of the pair name, which is what decides
    whether a price movement is already money or needs converting.
    """
    lot_001 = contract_size * 0.01
    quote = symbol[3:6].upper() if len(symbol) >= 6 else "USD"
    return InstrumentSpec(symbol, AssetClass.forex, "unit", 1.0,
                          lot_001, lot_001, contract_size, quote_currency=quote)


def _metal(symbol: str, contract_size: float) -> InstrumentSpec:
    lot_001 = contract_size * 0.01
    return InstrumentSpec(symbol, AssetClass.metals, "ounce", 1.0,
                          lot_001, lot_001, contract_size)


def _index_cfd(symbol: str, contract_size: float = 1.0) -> InstrumentSpec:
    lot_001 = contract_size * 0.01
    return InstrumentSpec(symbol, AssetClass.indices, "contract", 1.0,
                          lot_001, lot_001, contract_size)


def _energy(symbol: str, contract_size: float) -> InstrumentSpec:
    lot_001 = contract_size * 0.01
    return InstrumentSpec(symbol, AssetClass.commodities, "barrel", 1.0,
                          lot_001, lot_001, contract_size)


def _crypto_cfd(symbol: str, asset_class: AssetClass = AssetClass.crypto_major,
                contract_size: float = 1.0) -> InstrumentSpec:
    """Crypto CFD: contract size 1 coin, but a 0.01-lot floor — NOT the 8-decimal
    sizing a spot exchange allows. This is what makes crypto expensive to risk-size
    on a small CFD account."""
    lot_001 = contract_size * 0.01
    return InstrumentSpec(symbol, asset_class, "coin", 1.0,
                          lot_001, lot_001, contract_size)


# ---------------------------------------------------------------------------
# Exness — MT5 CFD broker. Symbol names follow Exness's own naming, which differs
# from other brokers' (US500/USTEC, not SPX500/NAS100; USOIL, not WTI).
# Exness does not offer exchange-listed futures, so MES/MCL/GC and friends are
# absent by design: asking for one fails closed rather than sizing a contract the
# account cannot trade.
# ---------------------------------------------------------------------------

_EXNESS_FX_MAJORS = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD")
_EXNESS_FX_CROSSES = ("EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "EURAUD", "EURCHF", "CADJPY")
_EXNESS_INDICES = ("US30", "US500", "USTEC", "DE30", "UK100", "JP225", "HK50", "AUS200", "FR40", "STOXX50")
_EXNESS_CRYPTO = ("BTCUSD", "ETHUSD", "XRPUSD", "LTCUSD", "BCHUSD", "SOLUSD", "ADAUSD")
_EXNESS_CRYPTO_MEME = ("DOGEUSD", "SHIBUSD")

_STANDARD_FX_LOT = 100_000.0
_CENT_FX_LOT = 1_000.0

EXNESS_STANDARD = BrokerProfile(
    key="exness_standard",
    name="Exness — Standard / Pro / Zero / Raw Spread",
    specs={
        **{s: _fx(s, _STANDARD_FX_LOT) for s in _EXNESS_FX_MAJORS + _EXNESS_FX_CROSSES},
        # 1 lot of gold = 100 oz, so 0.01 lot = 1 oz.
        "XAUUSD": _metal("XAUUSD", 100.0),
        # 1 lot of silver = 5,000 oz, so the 0.01-lot floor is *50 ounces*. The
        # increment, not the price, is what puts silver out of reach.
        "XAGUSD": _metal("XAGUSD", 5_000.0),
        "XPTUSD": _metal("XPTUSD", 100.0),
        "XPDUSD": _metal("XPDUSD", 100.0),
        **{s: _index_cfd(s) for s in _EXNESS_INDICES},
        # 1 lot of USOIL = 1,000 barrels, so 0.01 lot = 10 barrels.
        "USOIL": _energy("USOIL", 1_000.0),
        "UKOIL": _energy("UKOIL", 1_000.0),
        **{s: _crypto_cfd(s) for s in _EXNESS_CRYPTO},
        **{s: _crypto_cfd(s, AssetClass.crypto_meme) for s in _EXNESS_CRYPTO_MEME},
    },
    class_defaults={
        AssetClass.forex: replace(_fx("*forex", _STANDARD_FX_LOT), is_class_default=True),
        AssetClass.metals: replace(_metal("*metals", 100.0), is_class_default=True),
        AssetClass.indices: replace(_index_cfd("*indices"), is_class_default=True),
        AssetClass.commodities: replace(_energy("*commodities", 1_000.0), is_class_default=True),
        AssetClass.crypto_major: replace(_crypto_cfd("*crypto_major"), is_class_default=True),
        AssetClass.crypto_meme: replace(
            _crypto_cfd("*crypto_meme", AssetClass.crypto_meme), is_class_default=True),
        AssetClass.stocks: replace(
            InstrumentSpec("*stocks", AssetClass.stocks, "share", 1.0, 0.01, 0.01, 1.0),
            is_class_default=True),
    },
    notes=(
        "No exchange-listed futures. Requests for MES/MCL/GC fail closed.",
        "Crypto is a CFD with a 0.01-lot floor, NOT 8-decimal spot sizing.",
        "CFDs are rolling positions: no T+1 settlement, no PDT rule, no good-faith "
        "violations — and therefore no external cap on trades per day.",
        "Exness does not onboard US retail clients; the FINRA/PDT/T+1 rules written "
        "for a US cash equity account do not govern this account.",
    ),
)

# Exness Standard Cent: the lot is 1,000 units instead of 100,000, so every
# minimum and step is 100x finer. This is the single most important fact for a
# small account — it is what makes $50 forex viable at all.
EXNESS_CENT = BrokerProfile(
    key="exness_cent",
    name="Exness — Standard Cent",
    specs={
        **{s: _fx(s, _CENT_FX_LOT) for s in _EXNESS_FX_MAJORS + _EXNESS_FX_CROSSES},
        "XAUUSD": _metal("XAUUSD", 1.0),
        "XAGUSD": _metal("XAGUSD", 50.0),
    },
    class_defaults={
        AssetClass.forex: replace(_fx("*forex", _CENT_FX_LOT), is_class_default=True),
        AssetClass.metals: replace(_metal("*metals", 1.0), is_class_default=True),
    },
    notes=(
        "Cent-account instrument coverage is narrower than Standard — verify which "
        "symbols your account actually offers before planning around them.",
        "A cent lot is 1,000 units, so 0.01 lot = 10 units of base currency.",
    ),
)


# ---------------------------------------------------------------------------
# Generic profile: spot-exchange crypto and exchange-listed futures. Kept for
# backtesting against exchange data and for comparison against the broker's terms.
# ---------------------------------------------------------------------------

def _future(symbol: str, asset_class: AssetClass, point_value: float) -> InstrumentSpec:
    return InstrumentSpec(symbol, asset_class, "contract", point_value, 1.0, 1.0, 1.0,
                          verified=True)  # exchange-defined multipliers


GENERIC = BrokerProfile(
    key="generic",
    name="Generic — spot exchanges and exchange-listed futures",
    lot_based=False,
    specs={
        **{s: _fx(s, _STANDARD_FX_LOT) for s in _EXNESS_FX_MAJORS},
        "XAUUSD": _metal("XAUUSD", 100.0),
        "XAGUSD": _metal("XAGUSD", 5_000.0),
        **{s: _index_cfd(s) for s in ("US30", "NAS100", "SPX500", "US2000")},
        # Spot-exchange crypto: 8-decimal sizing, which is what lets any budget fit.
        **{s: InstrumentSpec(s, AssetClass.crypto_major, "coin", 1.0,
                            0.0001, 0.00001, 1.0)
           for s in ("BTCUSD", "ETHUSD", "BTCUSDT", "ETHUSDT")},
        "MES": _future("MES", AssetClass.indices, 5.0),
        "ES": _future("ES", AssetClass.indices, 50.0),
        "MNQ": _future("MNQ", AssetClass.indices, 2.0),
        "NQ": _future("NQ", AssetClass.indices, 20.0),
        "MYM": _future("MYM", AssetClass.indices, 0.5),
        "MCL": _future("MCL", AssetClass.commodities, 100.0),
        "CL": _future("CL", AssetClass.commodities, 1_000.0),
        "MGC": _future("MGC", AssetClass.commodities, 10.0),
        "GC": _future("GC", AssetClass.commodities, 100.0),
    },
    class_defaults={
        AssetClass.forex: replace(_fx("*forex", _STANDARD_FX_LOT), is_class_default=True),
        AssetClass.metals: replace(_metal("*metals", 100.0), is_class_default=True),
        AssetClass.indices: replace(_index_cfd("*indices"), is_class_default=True),
        AssetClass.commodities: replace(_energy("*commodities", 1_000.0), is_class_default=True),
        AssetClass.crypto_major: replace(
            InstrumentSpec("*crypto_major", AssetClass.crypto_major, "coin", 1.0,
                           0.0001, 0.00001, 1.0), is_class_default=True),
        AssetClass.crypto_meme: replace(
            InstrumentSpec("*crypto_meme", AssetClass.crypto_meme, "token", 1.0,
                           1.0, 1.0, 1.0), is_class_default=True),
        AssetClass.stocks: replace(
            InstrumentSpec("*stocks", AssetClass.stocks, "share", 1.0, 1.0, 1.0, 1.0),
            is_class_default=True),
    },
)


PROFILES: dict[str, BrokerProfile] = {
    p.key: p for p in (EXNESS_STANDARD, EXNESS_CENT, GENERIC)
}

# Backwards-compatible aliases so `broker="exness"` resolves to the live default.
PROFILE_ALIASES = {"exness": "exness_standard", "exness_standard_cent": "exness_cent"}


class UnknownInstrument(LookupError):
    """Raised where guessing a spec would be unsafe (live execution adapters)."""


class UnknownBrokerProfile(LookupError):
    pass


def _active_profile_key() -> str:
    # Imported lazily: app.config pulls in pydantic-settings, and this module is
    # imported by pure-function code paths that should stay cheap.
    from app.config import settings
    account = (settings.account_type or "").strip().lower()
    broker = (settings.broker or "generic").strip().lower()
    key = f"{broker}_{account}" if account else broker
    for candidate in (key, PROFILE_ALIASES.get(key, ""), broker, PROFILE_ALIASES.get(broker, "")):
        if candidate in PROFILES:
            return candidate
    raise UnknownBrokerProfile(
        f"No broker profile for broker={broker!r} account_type={account!r}. "
        f"Known profiles: {sorted(PROFILES)}"
    )


def profile_for(broker: str | None = None) -> BrokerProfile:
    if broker is None:
        return PROFILES[_active_profile_key()]
    key = broker.strip().lower()
    key = key if key in PROFILES else PROFILE_ALIASES.get(key, key)
    if key not in PROFILES:
        raise UnknownBrokerProfile(f"Unknown broker profile {broker!r}. Known: {sorted(PROFILES)}")
    return PROFILES[key]


def spec_for(instrument: str, asset_class: AssetClass | None = None,
             broker: str | None = None) -> InstrumentSpec | None:
    """Exact symbol match, else the asset-class default, else None.

    Pass `asset_class` wherever a sensible fallback beats refusing to size (the
    risk manager). Omit it where a wrong guess would place a real order at the
    wrong size (execution adapters) and handle None by failing closed.

    Returns None when the broker does not offer the asset class at all — asking
    Exness for an MES contract, or a Cent account for crypto.
    """
    profile = profile_for(broker)
    exact = profile.specs.get(instrument.upper())
    if exact is not None:
        return exact
    if asset_class is not None:
        return profile.class_defaults.get(asset_class)
    return None


def require_spec(instrument: str, asset_class: AssetClass | None = None,
                 broker: str | None = None) -> InstrumentSpec:
    spec = spec_for(instrument, asset_class, broker)
    if spec is None:
        raise UnknownInstrument(
            f"No contract specification for {instrument!r} on profile "
            f"{profile_for(broker).key!r}. Add it to app/risk/instruments.py rather "
            f"than letting a size be guessed."
        )
    return spec


def point_value_for(instrument: str, asset_class: AssetClass | None = None,
                    broker: str | None = None) -> float:
    """Money per 1.00 of price movement per unit of size. Defaults to 1.0 for
    unknown symbols, which is correct for every spot/CFD instrument and keeps P&L
    math unchanged for anything not in the registry."""
    spec = spec_for(instrument, asset_class, broker)
    return spec.point_value if spec is not None else 1.0


def quote_conversion(spec: InstrumentSpec, entry: float,
                     account_currency: str = "USD",
                     explicit_rate: float | None = None) -> float | None:
    """Multiplier converting one unit of quote-currency movement into account money.

    Returns None when the rate cannot be established, so callers fail closed rather
    than mis-size. Three cases:

    * Quote currency == account currency (EURUSD, XAUUSD on a USD account) -> 1.0.
    * The pair IS the conversion (USDJPY, USDCAD, USDCHF on a USD account): the
      account currency is the base, so one unit of quote buys 1/price of it.
    * A cross (EURJPY, GBPJPY) needs a third rate that cannot be derived from this
      pair's own price -> None unless supplied explicitly.
    """
    if explicit_rate is not None:
        return explicit_rate if explicit_rate > 0 else None

    quote = (spec.quote_currency or "").upper()
    account = (account_currency or "USD").upper()
    if quote == account:
        return 1.0

    if spec.asset_class == AssetClass.forex and len(spec.symbol) >= 6:
        base = spec.symbol[0:3].upper()
        if base == account and entry > 0:
            return 1.0 / entry

    return None


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


def size_for_risk(risk_amount: float, stop_distance: float, spec: InstrumentSpec,
                  quote_rate: float = 1.0) -> float:
    """The whole point of this module: risk budget -> tradeable position size.

    `quote_rate` converts quote-currency movement into account money — 1.0 when the
    quote currency is the account currency. Get it from `quote_conversion`.
    """
    if stop_distance <= 0 or spec.point_value <= 0 or quote_rate <= 0:
        return 0.0
    return round_size(risk_amount / (stop_distance * spec.point_value * quote_rate), spec)


def risk_for_size(stop_distance: float, size: float, spec: InstrumentSpec,
                  quote_rate: float = 1.0) -> float:
    """Money actually at risk for a given size. After flooring to the step this is
    lower than the requested budget, and it is what portfolio/correlation caps
    must be summed on — otherwise exposure is overstated."""
    return abs(stop_distance) * size * spec.point_value * quote_rate


def min_account_for(stop_distance: float, spec: InstrumentSpec, risk_pct: float,
                    quote_rate: float = 1.0) -> float:
    """Account equity at which the SMALLEST tradeable position equals `risk_pct`.

    The honest affordability answer: below this, the instrument cannot be traded
    without breaking the risk rule, regardless of leverage.
    """
    if risk_pct <= 0:
        return float("inf")
    return (stop_distance * spec.min_size * spec.point_value * quote_rate) / risk_pct


def units_to_lots(size: float, spec: InstrumentSpec) -> float:
    """Convert internal units to broker lots for lot-based APIs (e.g. MT5 volume)."""
    if spec.contract_size <= 0:
        return size
    return size / spec.contract_size


def lots_to_units(lots: float, spec: InstrumentSpec) -> float:
    return lots * spec.contract_size


# Legacy module-level names, kept so existing imports keep working. These are the
# GENERIC profile's tables; prefer spec_for()/profile_for() so the configured
# broker is honoured.
INSTRUMENTS = GENERIC.specs
CLASS_DEFAULTS = GENERIC.class_defaults
