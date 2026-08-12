#!/usr/bin/env python3
"""Reconcile the instrument registry against the broker's live contract specs.

Why you must run this before trading real money
-----------------------------------------------
Every non-futures spec in app/risk/instruments.py is recorded from broker
documentation, not read from your account. Brokers change specs, and vary them by
region, account type and symbol suffix (Exness appends suffixes like `m`/`z` on
some account types, so "EURUSDm" and "EURUSD" can carry different terms).

A wrong `contract_size` produces a wrong position size that nothing else in the
system can detect: the order is accepted, the risk gate believes it sized to 1%,
and the real exposure is off by the ratio of the error. MT5 publishes the
authoritative numbers per symbol, so this compares them field by field.

Usage
-----
Run on the machine where MetaTrader 5 is installed and logged in to the account
you will trade:

    python scripts/verify_broker_specs.py                       # reconcile active profile
    python scripts/verify_broker_specs.py --broker exness_cent
    python scripts/verify_broker_specs.py --suffix m            # symbols named EURUSDm
    python scripts/verify_broker_specs.py --json specs.json     # dump raw broker specs
    python scripts/verify_broker_specs.py --all-symbols --json all.json

Exit code is non-zero when any mismatch is found, so it can gate a deploy.

How MT5's fields map onto ours
------------------------------
    contract_size (units per lot)  <- trade_contract_size
    min_size      (units)          <- volume_min  * trade_contract_size
    size_step     (units)          <- volume_step * trade_contract_size
    point_value   (money/1.00/unit)<- (trade_tick_value / trade_tick_size)
                                      / trade_contract_size

The point_value derivation is the subtle one: MT5 quotes tick value per LOT, so it
must be divided by the contract size to reach our per-unit convention.
"""

from __future__ import annotations

import argparse
import json
import sys

from app.risk.instruments import InstrumentSpec, profile_for

# Relative tolerance for float comparisons of broker-reported values.
TOL = 1e-6


def _load_mt5():
    try:
        import MetaTrader5 as mt5
    except ImportError:
        sys.exit(
            "MetaTrader5 package not available on this platform.\n"
            "Run this on the Windows host where MT5 is installed and logged in, then\n"
            "use --json to export the broker's specs and reconcile them anywhere."
        )
    if not mt5.initialize():
        sys.exit(f"MT5 initialize() failed: {mt5.last_error()}")
    return mt5


def broker_spec(mt5, symbol: str) -> dict | None:
    """Read one symbol's contract terms, in our units convention."""
    if not mt5.symbol_select(symbol, True):
        return None
    info = mt5.symbol_info(symbol)
    if info is None:
        return None

    contract_size = float(info.trade_contract_size)
    tick_size = float(info.trade_tick_size) or float(info.point)
    tick_value = float(info.trade_tick_value)

    point_value = None
    if tick_size and contract_size:
        point_value = (tick_value / tick_size) / contract_size

    return {
        "symbol": symbol,
        "contract_size": contract_size,
        "min_size": float(info.volume_min) * contract_size,
        "size_step": float(info.volume_step) * contract_size,
        "point_value": point_value,
        # Context that does not affect sizing but does affect whether you can trade:
        "volume_min_lots": float(info.volume_min),
        "volume_step_lots": float(info.volume_step),
        "volume_max_lots": float(info.volume_max),
        "digits": int(info.digits),
        "trade_mode": int(info.trade_mode),
        "currency_profit": info.currency_profit,
        "currency_margin": info.currency_margin,
    }


def compare(spec: InstrumentSpec, live: dict) -> list[str]:
    """Field-by-field diff, phrased so the consequence is obvious."""
    problems = []

    def check(field: str, ours: float, theirs: float | None) -> None:
        if theirs is None:
            problems.append(f"{field}: broker did not report a value")
            return
        if ours == 0 and theirs == 0:
            return
        denom = max(abs(ours), abs(theirs), 1e-12)
        if abs(ours - theirs) / denom > TOL:
            ratio = theirs / ours if ours else float("inf")
            problems.append(
                f"{field}: registry {ours:g}, broker {theirs:g} "
                f"(off by {ratio:.6g}x)"
            )

    check("contract_size", spec.contract_size, live["contract_size"])
    check("min_size", spec.min_size, live["min_size"])
    check("size_step", spec.size_step, live["size_step"])
    check("point_value", spec.point_value, live["point_value"])

    # trade_mode 0 == SYMBOL_TRADE_MODE_DISABLED
    if live["trade_mode"] == 0:
        problems.append("trade_mode: symbol is DISABLED for trading on this account")
    if live["currency_profit"] != "USD":
        problems.append(
            f"currency_profit is {live['currency_profit']}, not USD — risk figures are "
            f"in that currency and need conversion before they mean dollars"
        )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--broker", default=None,
                        help="Profile to reconcile (default: the configured one)")
    parser.add_argument("--suffix", default="",
                        help="Symbol suffix this account uses, e.g. 'm' for EURUSDm")
    parser.add_argument("--json", metavar="PATH", default=None,
                        help="Write the broker's raw specs to PATH")
    parser.add_argument("--all-symbols", action="store_true",
                        help="Dump every symbol the account offers, not just registry ones")
    args = parser.parse_args()

    profile = profile_for(args.broker)
    mt5 = _load_mt5()

    account = mt5.account_info()
    print(f"Profile : {profile.key} ({profile.name})")
    if account is not None:
        print(f"Account : {account.login} @ {account.server} "
              f"[{account.currency}, leverage 1:{account.leverage}]")
    print()

    dump: dict[str, dict] = {}
    failed = False

    if args.all_symbols:
        for info in mt5.symbols_get() or []:
            live = broker_spec(mt5, info.name)
            if live:
                dump[info.name] = live
        print(f"Dumped {len(dump)} symbols.")
    else:
        ok, mismatched, missing = 0, 0, 0
        for symbol, spec in sorted(profile.specs.items()):
            broker_symbol = f"{symbol}{args.suffix}"
            live = broker_spec(mt5, broker_symbol)
            if live is None:
                print(f"  MISSING   {broker_symbol}: not offered on this account")
                missing += 1
                continue
            dump[broker_symbol] = live
            problems = compare(spec, live)
            if problems:
                mismatched += 1
                print(f"  MISMATCH  {broker_symbol}")
                for p in problems:
                    print(f"              {p}")
            else:
                ok += 1
                print(f"  ok        {broker_symbol}")

        print()
        print(f"{ok} matched, {mismatched} mismatched, {missing} not offered.")
        failed = bool(mismatched or missing)
        if failed:
            print(
                "\nFix app/risk/instruments.py before trading: a wrong contract_size or\n"
                "min_size silently mis-sizes every position in that instrument. Set\n"
                "verified=True on each spec once it matches."
            )

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(dump, fh, indent=2, sort_keys=True)
        print(f"\nWrote {args.json}")

    mt5.shutdown()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
