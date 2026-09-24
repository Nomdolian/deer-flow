"""Position sizing: fractional Kelly, capped, and then capped again by what
the book can actually absorb.

Kelly assumes you know p. You do not — you have an estimate with error bars,
which is why the fraction is a quarter and the hard cap usually binds first.
"""

from __future__ import annotations

import math


def kelly_shares(
    edge: float,
    price: float,
    equity: float,
    kelly_fraction: float = 0.25,
    cap_pct: float = 0.02,
) -> float:
    """Shares to buy at `price` given a per-share edge, on a binary payoff.

    Win pays (1 - price) per share, loss costs price per share.
    """
    if price <= 0 or price >= 1 or equity <= 0:
        return 0.0
    b = (1.0 - price) / price
    p = min(max(price + edge, 0.0), 1.0)
    f = (p * (b + 1.0) - 1.0) / b
    if f <= 0:
        return 0.0
    stake = min(equity * f * kelly_fraction, equity * cap_pct)
    return stake / price


def cap_by_depth(shares: float, book, side: str, max_book_fraction: float = 0.25) -> float:
    """Never take more than a quarter of the visible depth.

    Failure mode #3: the headline liquidity number is not the book. Size
    against the levels you can actually see.
    """
    levels = book.levels("SELL" if side.upper() == "BUY" else "BUY")
    visible = sum(level.size for level in levels[:3])
    if visible <= 0:
        return 0.0
    return min(shares, visible * max_book_fraction)


def floor_shares(shares: float, decimals: int = 2) -> float:
    """Always round size *down*. Rounding up walks straight through a cap."""
    factor = 10 ** decimals
    return math.floor(max(0.0, shares) * factor) / factor


def round_to_min_size(shares: float, price: float, min_order_size: float) -> float:
    """Below the market's minimum, the correct size is zero, not the minimum."""
    if shares * price < min_order_size:
        return 0.0
    return floor_shares(shares)
