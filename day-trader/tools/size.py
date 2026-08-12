#!/usr/bin/env python3
"""
Multi-asset position size calculator — AI Day Trader OS

Size comes from stop distance, never from "how much can I buy."

That principle is universal. What is NOT universal is how a stop distance turns
into a position: a share moves $1 per $1, an MES contract moves $5 per index
point, 1,000 units of EURUSD move $0.10 per pip, and gold moves $1 per ounce.
Get that translation wrong and a "1% risk" trade can be a 40% risk trade.

This tool holds one instrument registry so the translation is data, not guesswork.

Universal math (every asset class):

    risk budget      = account x risk%
    risk per unit    = |entry - stop| x point_value x quote_usd
    units by risk    = floor_to_increment(risk budget / risk per unit)
    capital per unit = margin_per_unit  OR  notional_per_unit / leverage
    units by capital = floor_to_increment(available / capital per unit)
    UNITS            = min(units by risk, units by capital)

Usage:
    python3 size.py --entry 10.50 --stop 10.20 --target 11.10
    python3 size.py --instrument crypto-spot --entry 63736 --stop 62500 --target 66200
    python3 size.py --instrument forex-nano --symbol EURUSD --entry 1.1540 --stop 1.1520
    python3 size.py --instrument xauusd --entry 4408 --stop 4390
    python3 size.py --instrument mes --entry 6800 --stop 6790 --margin-per-unit 50
    python3 size.py --list
"""

import argparse
import sys
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR

DEFAULT_ACCOUNT = 50.00
DEFAULT_RISK_PCT = 1.0
MAX_RISK_PCT = 3.0
MIN_RR = 2.0


# ---------------------------------------------------------------------------
# Instrument registry
#
# point_value : USD per 1.00 of quoted price movement, per one unit
# increment   : smallest tradeable size step
# leverage    : 1.0 = cash/spot (full notional required). >1 = margin product.
# margin_per_unit : if the broker publishes a fixed per-contract margin, pass it
#                   with --margin-per-unit. Futures margins change and are
#                   broker-specific, so none are hardcoded here.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Instrument:
    key: str
    asset_class: str
    unit: str
    point_value: float
    increment: Decimal
    leverage: float = 1.0
    session: str = ""
    settlement: str = ""
    corr_group: str = ""
    min_notional: float | None = None
    margin_required: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def plural(self) -> str:
        return self.unit if self.unit.endswith("s") else self.unit + "s"


def price_decimals(ref: float, inst: Instrument) -> int:
    """Quote precision differs by market: FX runs to the 5th decimal, equities to
    the cent, and sub-dollar instruments need more than either.

    `ref` should be the INSTRUMENT's price (entry), not the value being printed —
    a stop distance of 0.30 on a $10 stock is still a 2-decimal quote, and deriving
    precision from the distance itself would render it as 0.300000.
    """
    if inst.asset_class == "forex":
        return 3 if ref >= 20 else 5            # JPY-quoted pairs vs the rest
    if ref < 1:
        return 6
    return 2


def px(value: float, decimals: int) -> str:
    return f"{value:,.{decimals}f}"


def money(value: float) -> str:
    """Dollar amounts to the cent, with extra precision only when the figure is
    smaller than a cent — a nano-lot pip is $0.001, and rounding it to $0.00
    would hide the entire reason nano lots make a $50 forex account viable."""
    a = abs(value)
    if a and a < 0.001:
        return f"${value:,.6f}"
    if a and a < 0.01:
        return f"${value:,.4f}"
    return f"${value:,.2f}"


def unit_label(count, inst: Instrument) -> str:
    return inst.unit if abs(float(count)) == 1 else inst.plural


REGISTRY: dict[str, Instrument] = {
    # ---------------- Equities & ETFs ----------------
    "stock": Instrument(
        "stock", "stocks", "share", 1.0, Decimal("1"), 1.0,
        "09:30-16:00 ET, Mon-Fri", "T+1 (cash account)", "",
        notes=("Whole shares only — rounding waste is normal, never widen the stop to absorb it.",),
    ),
    "stock-fractional": Instrument(
        "stock-fractional", "stocks", "share", 1.0, Decimal("0.0001"), 1.0,
        "09:30-16:00 ET, Mon-Fri", "T+1 (cash account)", "",
        notes=("Fractional orders are often market-only — if your broker cannot hold a REAL "
               "stop on a fractional position, rule 7 says do not use fractionals.",),
    ),
    "etf": Instrument(
        "etf", "stocks", "share", 1.0, Decimal("1"), 1.0,
        "09:30-16:00 ET, Mon-Fri", "T+1 (cash account)", "",
        notes=("ETFs are the realistic route to metals/commodities/indices on a small cash account.",),
    ),

    # ---------------- Crypto ----------------
    "crypto-spot": Instrument(
        "crypto-spot", "crypto", "coin", 1.0, Decimal("0.00000001"), 1.0,
        "24/7 including weekends", "instant (no settlement cap)", "crypto_majors",
        min_notional=1.0,
        notes=("Native fractional units mean ZERO rounding waste — the one class where a "
               "$50 account can use its whole risk budget exactly.",
               "24/7 means 'flat by close' has no market-defined close. YOU must define a "
               "hard session end and be flat by it.",
               "Instant settlement removes the T+1 brake that was quietly capping you at one "
               "trade a day. The max-trades rule is now the ONLY thing stopping overtrading."),
    ),
    "crypto-perp": Instrument(
        "crypto-perp", "crypto", "contract", 1.0, Decimal("0.001"), 1.0,
        "24/7 including weekends", "instant, funding paid periodically", "crypto_majors",
        notes=("Perpetuals add funding costs and liquidation risk on top of your stop. "
               "Pass --leverage explicitly; the default of 1.0 is not what your exchange gives you.",
               "At a $50 account this is the fastest available way to zero. The docs recommend spot."),
    ),

    # ---------------- Forex (unit = units of BASE currency) ----------------
    "forex-nano": Instrument(
        "forex-nano", "forex", "unit", 1.0, Decimal("100"), 30.0,
        "24/5, Sun 17:00 ET - Fri 17:00 ET", "rolling spot, no GFV concept", "usd_majors",
        margin_required=True,
        notes=("100-unit increments. On EURUSD 1 pip on 100 units = $0.01 — small enough "
               "that a $0.50 risk budget genuinely fits.",
               "Retail leverage caps differ by jurisdiction (US ~50:1 majors, EU/UK 30:1). "
               "Default here is 30 — verify yours."),
    ),
    "forex-micro": Instrument(
        "forex-micro", "forex", "unit", 1.0, Decimal("1000"), 30.0,
        "24/5, Sun 17:00 ET - Fri 17:00 ET", "rolling spot, no GFV concept", "usd_majors",
        margin_required=True,
        notes=("1,000-unit (0.01 lot) increments. On EURUSD 1 pip = $0.10.",),
    ),
    "forex-mini": Instrument(
        "forex-mini", "forex", "unit", 1.0, Decimal("10000"), 30.0,
        "24/5, Sun 17:00 ET - Fri 17:00 ET", "rolling spot, no GFV concept", "usd_majors",
        margin_required=True,
        notes=("10,000-unit (0.1 lot) increments. On EURUSD 1 pip = $1.00.",),
    ),
    "forex-standard": Instrument(
        "forex-standard", "forex", "unit", 1.0, Decimal("100000"), 30.0,
        "24/5, Sun 17:00 ET - Fri 17:00 ET", "rolling spot, no GFV concept", "usd_majors",
        margin_required=True,
        notes=("100,000-unit (1.0 lot) increments. On EURUSD 1 pip = $10.00.",),
    ),

    # ---------------- Spot metals ----------------
    "xauusd": Instrument(
        "xauusd", "metals", "ounce", 1.0, Decimal("1"), 20.0,
        "23/5, closes ~17:00 ET daily", "rolling spot", "metals",
        margin_required=True,
        notes=("Broker min is usually 0.01 lot = 1 oz. Gold near $4,400/oz means a typical "
               "$10-15 stop risks $10-15 on a SINGLE ounce — 20-30% of a $50 account.",
               "If 1 oz does not fit your risk budget, gold spot is not tradeable at your size. "
               "Use a gold ETF or skip the class."),
    ),
    "xagusd": Instrument(
        "xagusd", "metals", "ounce", 1.0, Decimal("50"), 20.0,
        "23/5, closes ~17:00 ET daily", "rolling spot", "metals",
        margin_required=True,
        notes=("1 lot = 5,000 oz, so min 0.01 lot = 50 OUNCES, not 1. At ~$66/oz that is "
               "~$3,300 notional and a $0.50 stop risks $25.",
               "The increment, not the price, is what makes silver spot unreachable on a small account."),
    ),

    # ---------------- Index futures ----------------
    "mes": Instrument("mes", "indices", "contract", 5.0, Decimal("1"), 1.0,
                      "nearly 23/5 (CME Globex)", "futures margin", "us_indices",
                      margin_required=True, notes=("Micro E-mini S&P 500. $5 per index point.",)),
    "es": Instrument("es", "indices", "contract", 50.0, Decimal("1"), 1.0,
                     "nearly 23/5 (CME Globex)", "futures margin", "us_indices",
                     margin_required=True, notes=("E-mini S&P 500. $50 per index point.",)),
    "mnq": Instrument("mnq", "indices", "contract", 2.0, Decimal("1"), 1.0,
                      "nearly 23/5 (CME Globex)", "futures margin", "us_indices",
                      margin_required=True, notes=("Micro E-mini Nasdaq-100. $2 per index point.",)),
    "nq": Instrument("nq", "indices", "contract", 20.0, Decimal("1"), 1.0,
                     "nearly 23/5 (CME Globex)", "futures margin", "us_indices",
                     margin_required=True, notes=("E-mini Nasdaq-100. $20 per index point.",)),
    "mym": Instrument("mym", "indices", "contract", 0.5, Decimal("1"), 1.0,
                      "nearly 23/5 (CME Globex)", "futures margin", "us_indices",
                      margin_required=True, notes=("Micro E-mini Dow. $0.50 per index point — "
                                                   "the smallest tick value in US index futures.",)),
    "m2k": Instrument("m2k", "indices", "contract", 5.0, Decimal("1"), 1.0,
                      "nearly 23/5 (CME Globex)", "futures margin", "us_indices",
                      margin_required=True, notes=("Micro E-mini Russell 2000. $5 per index point.",)),

    # ---------------- Commodity futures ----------------
    "mcl": Instrument("mcl", "commodities", "contract", 100.0, Decimal("1"), 1.0,
                      "nearly 23/5 (CME Globex)", "futures margin", "energy",
                      margin_required=True, notes=("Micro WTI crude, 100 bbl. $100 per $1.00 of oil; "
                                                   "$1 per $0.01 tick. WTI near $82.",)),
    "cl": Instrument("cl", "commodities", "contract", 1000.0, Decimal("1"), 1.0,
                     "nearly 23/5 (CME Globex)", "futures margin", "energy",
                     margin_required=True, notes=("WTI crude, 1,000 bbl. $1,000 per $1.00 move.",)),
    "mgc": Instrument("mgc", "commodities", "contract", 10.0, Decimal("1"), 1.0,
                      "nearly 23/5 (CME Globex)", "futures margin", "metals",
                      margin_required=True, notes=("Micro gold, 10 oz. $10 per $1.00 of gold.",)),
    "gc": Instrument("gc", "commodities", "contract", 100.0, Decimal("1"), 1.0,
                     "nearly 23/5 (CME Globex)", "futures margin", "metals",
                     margin_required=True, notes=("Gold, 100 oz. $100 per $1.00 of gold.",)),
    "sil": Instrument("sil", "commodities", "contract", 1000.0, Decimal("1"), 1.0,
                      "nearly 23/5 (CME Globex)", "futures margin", "metals",
                      margin_required=True, notes=("Micro silver, 1,000 oz. $1,000 per $1.00 of silver.",)),
    "ng": Instrument("ng", "commodities", "contract", 10000.0, Decimal("1"), 1.0,
                     "nearly 23/5 (CME Globex)", "futures margin", "energy",
                     margin_required=True, notes=("Natural gas, 10,000 MMBtu. $10,000 per $1.00 move.",)),
    "zc": Instrument("zc", "commodities", "contract", 50.0, Decimal("1"), 1.0,
                     "CME grain session", "futures margin", "grains",
                     margin_required=True, notes=("Corn, 5,000 bu, quoted in CENTS per bushel. "
                                                  "$50 per 1.00 cent move — pass entry/stop in cents.",)),
    "zw": Instrument("zw", "commodities", "contract", 50.0, Decimal("1"), 1.0,
                     "CME grain session", "futures margin", "grains",
                     margin_required=True, notes=("Wheat, 5,000 bu, quoted in CENTS per bushel.",)),

    # ---------------- Generic CFD escape hatch ----------------
    "cfd": Instrument(
        "cfd", "cfd", "unit", 1.0, Decimal("0.01"), 20.0,
        "instrument-specific", "rolling, financing charged", "",
        margin_required=True,
        notes=("Generic CFD. You MUST pass --point-value and --increment from your broker's "
               "contract specification — CFD specs are not standardized between brokers.",
               "CFDs are not available to US retail traders.",),
    ),
}

ALIASES = {
    "shares": "stock", "equity": "stock", "equities": "stock",
    "btc": "crypto-spot", "eth": "crypto-spot", "crypto": "crypto-spot",
    "forex": "forex-micro", "fx": "forex-micro",
    "gold": "xauusd", "xau": "xauusd", "silver": "xagusd", "xag": "xagusd",
    "wti": "mcl", "oil": "mcl", "corn": "zc", "wheat": "zw",
}


# ---------------------------------------------------------------------------
# Exness (MT5 CFD broker) — the account these trades are actually taken on.
#
# Specs differ per broker, and the differences decide what a small account can
# reach. Two that invert the generic conclusions:
#
#   * Crypto is a CFD with a 0.01-lot floor (0.01 BTC), NOT the 8-decimal spot
#     sizing above. That is ~$12 of risk on a realistic stop, so the "crypto uses
#     100% of the budget" result holds on a spot exchange and NOT here.
#   * A Standard Cent account's lot is 1,000 units, so 0.01 lot is 10 units of
#     base currency — 100x finer than Standard, and what makes $50 forex viable.
#
# Exness offers no exchange-listed futures, so MES/MCL/GC are absent by design.
# Every value here is recorded from broker documentation, NOT read from the
# account. Verify against MT5 symbol_info() before risking real money — see
# strategy/instruments.md.
# ---------------------------------------------------------------------------

EXNESS_STANDARD: dict[str, Instrument] = {
    "eurusd": Instrument("eurusd", "forex", "unit", 1.0, Decimal("1000"), 2000.0,
                         "24/5, Sun 22:05 - Fri 21:59 UTC (server time)", "rolling CFD",
                         "usd_majors", margin_required=True,
                         notes=("Exness Standard: 1 lot = 100,000 units, min 0.01 lot = 1,000 units.",
                                "A 20-pip stop on the 1,000-unit minimum risks $2.00 — needs a "
                                "~$200 account at 1%.")),
    "eurusd-cent": Instrument("eurusd-cent", "forex", "unit", 1.0, Decimal("10"), 2000.0,
                              "24/5, Sun 22:05 - Fri 21:59 UTC (server time)", "rolling CFD",
                              "usd_majors", margin_required=True,
                              notes=("Exness Standard CENT: 1 lot = 1,000 units, min 0.01 lot = "
                                     "10 units — 100x finer than Standard.",
                                     "A 20-pip stop on 10 units risks $0.02. THIS is the "
                                     "instrument a $50 account can actually risk-size.")),
    "xauusd-exness": Instrument("xauusd-exness", "metals", "ounce", 1.0, Decimal("1"), 2000.0,
                                "23/5, daily break", "rolling CFD", "metals",
                                margin_required=True,
                                notes=("Exness gold: 1 lot = 100 oz, min 0.01 lot = 1 oz.",
                                       "A $15 stop risks $15 on the minimum — needs ~$1,500.")),
    "xagusd-exness": Instrument("xagusd-exness", "metals", "ounce", 1.0, Decimal("50"), 2000.0,
                                "23/5, daily break", "rolling CFD", "metals",
                                margin_required=True,
                                notes=("Exness silver: 1 lot = 5,000 oz, so min 0.01 lot = 50 oz.",)),
    "btcusd-exness": Instrument("btcusd-exness", "crypto", "coin", 1.0, Decimal("0.01"), 200.0,
                                "24/7", "rolling CFD", "crypto_majors",
                                margin_required=True,
                                notes=("Exness crypto CFD: contract size 1, min 0.01 lot = 0.01 BTC.",
                                       "NOT spot 8-decimal sizing. ~$637 notional and ~$12 risk on "
                                       "a $1,236 stop — needs a ~$1,200 account at 1%.")),
    "us500": Instrument("us500", "indices", "contract", 1.0, Decimal("0.01"), 200.0,
                        "nearly 24/5", "rolling CFD", "us_indices", margin_required=True,
                        notes=("Exness names the S&P 500 US500 and the Nasdaq USTEC — not "
                               "SPX500/NAS100. Sizing off another broker's ticker means sizing "
                               "off specs that are not this account's.",)),
    "ustec": Instrument("ustec", "indices", "contract", 1.0, Decimal("0.01"), 200.0,
                        "nearly 24/5", "rolling CFD", "us_indices", margin_required=True,
                        notes=("Exness Nasdaq 100 CFD.",)),
    "usoil": Instrument("usoil", "commodities", "barrel", 1.0, Decimal("10"), 200.0,
                        "nearly 24/5", "rolling CFD", "energy", margin_required=True,
                        notes=("Exness WTI: 1 lot = 1,000 barrels, min 0.01 lot = 10 barrels.",
                               "A $0.50 stop risks $5 on the minimum — needs a ~$500 account.")),
}

REGISTRY.update(EXNESS_STANDARD)
ALIASES.update({
    "exness-fx": "eurusd", "exness-forex": "eurusd",
    "cent": "eurusd-cent", "exness-cent": "eurusd-cent", "forex-cent": "eurusd-cent",
    "exness-gold": "xauusd-exness", "exness-silver": "xagusd-exness",
    "exness-btc": "btcusd-exness", "exness-crypto": "btcusd-exness",
    "exness-oil": "usoil", "spx": "us500", "nasdaq": "ustec",
})


def resolve(key: str) -> Instrument:
    k = key.strip().lower()
    k = ALIASES.get(k, k)
    if k not in REGISTRY:
        raise ValueError(
            f"Unknown instrument '{key}'. Run --list to see all {len(REGISTRY)} instruments, "
            "or use --instrument cfd with --point-value/--increment for anything unlisted."
        )
    return REGISTRY[k]


def floor_to_increment(value: float, increment: Decimal) -> Decimal:
    """Floor a size down to a whole multiple of the tradeable increment."""
    if value <= 0:
        return Decimal(0)
    steps = (Decimal(str(value)) / increment).to_integral_value(rounding=ROUND_FLOOR)
    return steps * increment


def fmt_units(units: Decimal, increment: Decimal) -> str:
    decimals = max(0, -increment.as_tuple().exponent)
    return f"{units:,.{decimals}f}"


def size_position(account, risk_pct, entry, stop, settled_cash=None, target=None,
                  instrument="stock", point_value=None, increment=None,
                  leverage=None, margin_per_unit=None, quote_usd=1.0, symbol=None):
    """Return a dict describing the position and every constraint that bound it."""
    if entry <= 0 or stop <= 0:
        raise ValueError("Entry and stop must be positive prices.")
    if entry == stop:
        raise ValueError("Entry and stop cannot be equal — there is no defined risk.")
    if quote_usd <= 0:
        raise ValueError("--quote-usd must be positive.")

    inst = instrument if isinstance(instrument, Instrument) else resolve(instrument)
    pv = inst.point_value if point_value is None else point_value
    inc = inst.increment if increment is None else Decimal(str(increment))
    lev = inst.leverage if leverage is None else leverage
    if pv <= 0:
        raise ValueError("--point-value must be positive.")
    if inc <= 0:
        raise ValueError("--increment must be positive.")
    if lev <= 0:
        raise ValueError("--leverage must be positive.")

    available = account if settled_cash is None else settled_cash
    direction = "LONG" if stop < entry else "SHORT"

    stop_distance = abs(entry - stop)
    risk_per_unit = stop_distance * pv * quote_usd
    notional_per_unit = entry * pv * quote_usd
    risk_budget = account * (risk_pct / 100.0)

    units_by_risk = floor_to_increment(risk_budget / risk_per_unit, inc)

    # Capital constraint. Futures margin is broker-specific and changes, so if a
    # margin product has no published figure supplied we decline to invent one.
    capital_per_unit = None
    capital_unknown = False
    if margin_per_unit is not None:
        capital_per_unit = margin_per_unit
    elif inst.margin_required and leverage is None and inst.leverage == 1.0:
        capital_unknown = True
    else:
        capital_per_unit = notional_per_unit / lev

    if capital_unknown:
        units_by_capital = None
        units = units_by_risk
    else:
        units_by_capital = floor_to_increment(available / capital_per_unit, inc)
        units = min(units_by_risk, units_by_capital)

    if units_by_risk == 0:
        binding = f"risk budget (one {inst.unit} risks more than the whole budget)"
    elif capital_unknown:
        binding = "risk budget (capital requirement UNKNOWN — see warnings)"
    elif units_by_capital < units_by_risk:
        binding = "available capital"
    else:
        binding = "risk budget"

    actual_risk = float(units) * risk_per_unit
    notional = float(units) * notional_per_unit
    capital_used = None if capital_unknown else float(units) * capital_per_unit

    result = {
        "instrument": inst, "symbol": symbol or inst.key.upper(),
        "direction": direction, "entry": entry, "stop": stop, "target": target,
        "account": account, "available": available, "risk_pct": risk_pct,
        "risk_budget": risk_budget, "stop_distance": stop_distance,
        "point_value": pv, "increment": inc, "leverage": lev, "quote_usd": quote_usd,
        "risk_per_unit": risk_per_unit, "notional_per_unit": notional_per_unit,
        "capital_per_unit": capital_per_unit,
        "units_by_risk": units_by_risk, "units_by_capital": units_by_capital,
        "units": units, "binding_constraint": binding,
        "actual_risk": actual_risk,
        "actual_risk_pct": (actual_risk / account * 100) if account else 0.0,
        "budget_used_pct": (actual_risk / risk_budget * 100) if risk_budget else 0.0,
        "notional": notional,
        "notional_pct_of_account": (notional / account * 100) if account else 0.0,
        "capital_used": capital_used,
        # Explicit None check, not truthiness: a zero-size result has capital_used
        # of 0.0, which is falsy but must still render as 0%.
        "capital_pct_of_account": (capital_used / account * 100)
                                  if (capital_used is not None and account) else None,
        "capital_unknown": capital_unknown,
        "warnings": [],
    }

    if target is not None:
        reward = abs(target - entry)
        result["reward_per_unit"] = reward * pv * quote_usd
        result["rr"] = reward / stop_distance
        result["profit_at_target"] = float(units) * reward * pv * quote_usd
        if direction == "LONG" and target <= entry:
            result["warnings"].append("Target is at or below entry on a LONG. Check your numbers.")
        if direction == "SHORT" and target >= entry:
            result["warnings"].append("Target is at or above entry on a SHORT. Check your numbers.")
        # Tolerance guards float artifacts, e.g. 0.60/0.30 -> 1.9999999999999993
        if result["rr"] < MIN_RR - 1e-9:
            result["warnings"].append(
                f"Risk:reward is 1:{result['rr']:.2f} — below the 1:{MIN_RR:.0f} minimum. "
                "RULE SAYS SKIP THIS TRADE.")

    if risk_pct > MAX_RISK_PCT:
        result["warnings"].append(f"Risk {risk_pct}% exceeds the {MAX_RISK_PCT}% hard ceiling.")

    if units == 0:
        result["warnings"].append(
            f"POSITION SIZE IS ZERO. The smallest tradeable size ({fmt_units(inc, inc)} "
            f"{unit_label(inc, inst)}) risks ${risk_per_unit * float(inc):,.2f}, which exceeds your "
            f"${risk_budget:.2f} budget. This instrument does not fit the account at this stop "
            f"distance. Do NOT round up and do NOT tighten the stop to force it — pick a "
            f"different instrument or skip.")

    # Only worth saying when a position actually exists — at zero size the
    # zero-size warning is the actionable one and this is just noise.
    if capital_unknown and units > 0:
        result["warnings"].append(
            f"{inst.key.upper()} is a margin product and no per-unit margin was supplied, so the "
            f"capital check was SKIPPED — the size above is risk-based only. Day-trade margin is "
            f"broker-specific and changes; get the current figure and re-run with "
            f"--margin-per-unit. Notional here is ${notional:,.2f} "
            f"({result['notional_pct_of_account']:,.0f}% of the account), on a "
            f"${account:,.2f} account.")

    if units > 0 and units_by_capital is not None and units_by_capital < units_by_risk:
        result["warnings"].append(
            "Available capital capped the size below the risk-based size. The trade fits, but "
            "you are deploying most of the account.")

    if inst.min_notional and 0 < notional < inst.min_notional:
        result["warnings"].append(
            f"Notional ${notional:,.2f} is below the typical ${inst.min_notional:,.2f} exchange "
            f"minimum order size. The size is mathematically correct but may be rejected.")

    if lev > 1.0 and units > 0:
        result["warnings"].append(
            f"LEVERAGE {lev:g}:1 — controlling ${notional:,.2f} of notional on a ${account:,.2f} "
            f"account. Leverage changes the CAPITAL required, never the risk budget. Your risk is "
            f"still ${actual_risk:.2f} ONLY because the stop holds; a gap through it can lose more.")

    if inst.asset_class == "crypto" and units > 0:
        result["warnings"].append(
            "24/7 market: there is no closing bell to make you flat. Write down a session end "
            "time and a plan for the position while you sleep, or don't take it.")

    if inst.asset_class == "forex" and symbol and not symbol.upper().endswith("USD") and quote_usd == 1.0:
        result["warnings"].append(
            f"{symbol.upper()} is not USD-quoted but --quote-usd is 1.0, so this risk figure is in "
            f"the QUOTE currency, not dollars. Pass --quote-usd with the quote-to-USD rate.")

    # Cash/spot products only. On a margin product a >90% notional figure is normal
    # and is already covered by the leverage and margin-unknown warnings; reusing
    # this cash-account phrasing there would misdescribe what is happening.
    if (result["notional_pct_of_account"] > 90 and lev <= 1.0
            and not inst.margin_required and units > 0):
        msg = f"This deploys {result['notional_pct_of_account']:.0f}% of the account."
        if inst.settlement.startswith("T+"):
            msg += " Settled cash will be ~$0 until tomorrow."
        result["warnings"].append(msg)

    if 0 < result["budget_used_pct"] < 50:
        result["warnings"].append(
            f"Increment rounding means actual risk (${actual_risk:.2f}) uses only "
            f"{result['budget_used_pct']:.0f}% of your ${risk_budget:.2f} budget. Normal on a small "
            f"account — do NOT compensate by widening the stop.")

    return result


def render(r):
    inst = r["instrument"]
    u = inst.unit
    inc = r["increment"]
    dp = price_decimals(r["entry"], inst)
    out = []
    out.append("=" * 62)
    out.append(f"  POSITION SIZE — {r['direction']} {r['symbol']}  [{inst.asset_class}]")
    out.append("=" * 62)
    out.append(f"  Account            {money(r['account']):>13}")
    out.append(f"  Available capital  {money(r['available']):>13}")
    out.append(f"  Risk budget        {money(r['risk_budget']):>13}  ({r['risk_pct']:.2f}%)")
    out.append("-" * 62)
    out.append(f"  Entry              {px(r['entry'], dp):>13}")
    out.append(f"  Stop               {px(r['stop'], dp):>13}")
    out.append(f"  Stop distance      {px(r['stop_distance'], dp):>13}")
    if r.get("target") is not None:
        out.append(f"  Target             {px(r['target'], dp):>13}")
        out.append(f"  Risk:reward        {'1:' + format(r['rr'], '.2f'):>13}")
    out.append("-" * 62)
    out.append(f"  CONTRACT SPEC ({inst.key})")
    out.append(f"    1 {u} moves {money(r['point_value'])} per 1.00 of price")
    out.append(f"    min increment    {fmt_units(inc, inc)} {unit_label(inc, inst)}")
    out.append(f"    risk per {u:<9}{money(r['risk_per_unit']):>10}")
    if r["leverage"] > 1.0:
        out.append(f"    leverage         {r['leverage']:g}:1")
    out.append(f"    session          {inst.session}")
    out.append(f"    settlement       {inst.settlement}")
    if inst.corr_group:
        out.append(f"    correlation grp  {inst.corr_group}")
    out.append("-" * 62)
    out.append("  THE MATH")
    out.append(f"    by risk    = {money(r['risk_budget'])} / {money(r['risk_per_unit'])}"
               f" -> {fmt_units(r['units_by_risk'], inc)} {unit_label(r['units_by_risk'], inst)}")
    if r["units_by_capital"] is None:
        out.append("    by capital = UNKNOWN (no margin figure supplied)")
    else:
        cpu = r["capital_per_unit"]
        out.append(f"    by capital = {money(r['available'])} / {money(cpu)} per {u}"
                   f" -> {fmt_units(r['units_by_capital'], inc)}"
                   f" {unit_label(r['units_by_capital'], inst)}")
    out.append(f"    binding constraint: {r['binding_constraint']}")
    out.append("-" * 62)
    out.append(f"  >>> SIZE           {fmt_units(r['units'], inc):>13}"
               f" {unit_label(r['units'], inst)}")
    out.append(f"  Notional           {money(r['notional']):>13}"
               f"  ({r['notional_pct_of_account']:,.0f}% of account)")
    if r["capital_used"] is not None:
        out.append(f"  Capital required   {money(r['capital_used']):>13}"
                   f"  ({r['capital_pct_of_account']:,.0f}% of account)")
    out.append(f"  Actual risk        {money(r['actual_risk']):>13}"
               f"  ({r['actual_risk_pct']:.2f}% of account)")
    out.append(f"  Budget used        {r['budget_used_pct']:>12,.0f}%")
    if r.get("profit_at_target") is not None:
        out.append(f"  Profit at target   {money(r['profit_at_target']):>13}")
    out.append("=" * 62)

    if inst.notes:
        out.append("")
        for n in inst.notes:
            out.append(f"  * {n}")

    if r["warnings"]:
        out.append("")
        for w in r["warnings"]:
            out.append(f"  [!] {w}")

    out.append("")
    out.append("  Stop-loss order goes in IMMEDIATELY after fill. Real order.")
    return "\n".join(out)


def render_list():
    rows = sorted(REGISTRY.values(), key=lambda i: (i.asset_class, i.key))
    out = [f"{'INSTRUMENT':<18}{'CLASS':<13}{'UNIT':<10}{'$/POINT':>10}{'MIN SIZE':>12}  SESSION"]
    out.append("-" * 96)
    current = None
    for i in rows:
        if i.asset_class != current:
            current = i.asset_class
            out.append("")
        out.append(f"{i.key:<18}{i.asset_class:<13}{i.unit:<10}"
                   f"{i.point_value:>10,.2f}{fmt_units(i.increment, i.increment):>12}  {i.session}")
    out.append("")
    out.append("Aliases: " + ", ".join(f"{k}->{v}" for k, v in sorted(ALIASES.items())))
    out.append("")
    out.append("Anything unlisted: --instrument cfd --point-value X --increment Y "
               "(from your broker's contract spec).")
    return "\n".join(out)


def main():
    p = argparse.ArgumentParser(
        description="Position size from stop distance, across every asset class.")
    p.add_argument("--list", action="store_true", help="List all known instruments and exit")
    p.add_argument("--instrument", "-i", default="stock",
                   help="Instrument key or alias (default: stock). See --list.")
    p.add_argument("--symbol", default=None, help="Display label, e.g. EURUSD, BTCUSD, AAPL")
    p.add_argument("--entry", type=float)
    p.add_argument("--stop", type=float)
    p.add_argument("--target", type=float, default=None)
    p.add_argument("--account", type=float, default=DEFAULT_ACCOUNT)
    p.add_argument("--cash", type=float, default=None,
                   help="Capital available (defaults to account balance)")
    p.add_argument("--risk-pct", type=float, default=DEFAULT_RISK_PCT)
    p.add_argument("--point-value", type=float, default=None,
                   help="Override $ per 1.00 price move per unit")
    p.add_argument("--increment", type=float, default=None,
                   help="Override minimum tradeable size step")
    p.add_argument("--leverage", type=float, default=None, help="Override leverage")
    p.add_argument("--margin-per-unit", type=float, default=None,
                   help="Broker's margin per contract/unit (required for futures)")
    p.add_argument("--quote-usd", type=float, default=1.0,
                   help="Quote-currency-to-USD rate for non-USD-quoted pairs")
    args = p.parse_args()

    if args.list:
        print(render_list())
        return 0

    if args.entry is None or args.stop is None:
        p.error("--entry and --stop are required (or use --list)")

    try:
        r = size_position(args.account, args.risk_pct, args.entry, args.stop, args.cash,
                          args.target, args.instrument, args.point_value, args.increment,
                          args.leverage, args.margin_per_unit, args.quote_usd, args.symbol)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(render(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
