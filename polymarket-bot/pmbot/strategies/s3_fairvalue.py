"""S3 — fair value on crypto up/down markets.

Polymarket's hourly and 15-minute BTC/ETH markets price a probability that
is often stale against live spot. Under zero-drift GBM the probability that
spot finishes above strike K is Phi(d2) with d2 = ln(S/K) / (sigma*sqrt(tau)).

The model gives the number. Market structure, if wired in, only ever gives a
veto — never a signal. Thresholds are deliberately wide: crypto is the
highest-fee category (0.07) and the sigma estimate is noisy.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass

from pmbot.config import FairValueConfig
from pmbot.data.book import OrderBook
from pmbot.fees import fee_per_share
from pmbot.state import Context
from pmbot.strategies.base import BUY, SELL, BaseStrategy, Signal
from pmbot.universe import Market

SECONDS_PER_YEAR = 365.0 * 24.0 * 3600.0

SYMBOLS = {
    "bitcoin": "btcusdt",
    "btc": "btcusdt",
    "ethereum": "ethusdt",
    "eth": "ethusdt",
    "solana": "solusdt",
    "sol": "solusdt",
}

_STRIKE_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)")
_ABOVE_RE = re.compile(r"\b(above|over|greater than|up|higher)\b", re.IGNORECASE)
_BELOW_RE = re.compile(r"\b(below|under|less than|down|lower)\b", re.IGNORECASE)


@dataclass
class CryptoSpec:
    symbol: str
    strike: float
    above: bool  # True: token pays if spot > strike


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def probability_above(spot: float, strike: float, sigma: float, tau_years: float) -> float:
    """Phi(d2) under zero drift. Degenerate inputs collapse to the indicator,
    which is the correct limit as tau -> 0.
    """
    if spot <= 0 or strike <= 0:
        return 0.0
    if sigma <= 0 or tau_years <= 0:
        return 1.0 if spot > strike else 0.0
    d2 = math.log(spot / strike) / (sigma * math.sqrt(tau_years))
    return normal_cdf(d2)


def parse_crypto_market(market: Market) -> CryptoSpec | None:
    """Extract (symbol, strike, direction) from a market's wording.

    Returns None when anything is ambiguous — an unparsed market is skipped,
    never guessed at.
    """
    text = f"{market.question} {market.outcome}".lower()
    symbol = next((s for key, s in SYMBOLS.items() if key in text), None)
    if symbol is None:
        return None
    match = _STRIKE_RE.search(market.question)
    if match is None:
        return None
    try:
        strike = float(match.group(1).replace(",", ""))
    except ValueError:
        return None

    outcome = market.outcome.lower()
    if outcome in ("yes", "no"):
        above = bool(_ABOVE_RE.search(market.question)) and not _BELOW_RE.search(market.question)
        if not above and not _BELOW_RE.search(market.question):
            return None
        if outcome == "no":
            above = not above
    elif _ABOVE_RE.search(outcome):
        above = True
    elif _BELOW_RE.search(outcome):
        above = False
    else:
        return None
    return CryptoSpec(symbol=symbol, strike=strike, above=above)


class FairValueStrategy(BaseStrategy):
    name = "s3_fairvalue"

    def __init__(
        self,
        cfg: FairValueConfig,
        stale_book_max_ms: float = 1500,
        structure_veto: Callable[[str, str], bool] | None = None,
    ):
        self.cfg = cfg
        self.enabled = cfg.enabled
        self.stale_book_max_ms = stale_book_max_ms
        # structure_veto(symbol, side) -> True to suppress. Wire an SMC/ICT
        # read here; it may only ever remove trades, never add them.
        self.structure_veto = structure_veto

    def on_book(self, book: OrderBook, ctx: Context) -> list[Signal]:
        if not self.enabled:
            return []
        market = ctx.market(book.token_id)
        if market is None or book.is_stale(self.stale_book_max_ms, ctx.now):
            return []
        spec = parse_crypto_market(market)
        if spec is None:
            return []

        feed = ctx.spot.get(spec.symbol)
        if feed is None or feed.last_price is None:
            return []
        if feed.age_s(ctx.now) > 10:
            return []  # a stale spot feed is a stale model
        sigma = feed.annualised_vol(min_samples=self.cfg.min_samples)
        if sigma is None:
            return []

        seconds_left = market.end_ts - ctx.now
        if seconds_left <= 0 or seconds_left > self.cfg.max_minutes_to_resolution * 60:
            return []
        tau = seconds_left / SECONDS_PER_YEAR

        model_p = probability_above(feed.last_price, spec.strike, sigma, tau)
        if not spec.above:
            model_p = 1.0 - model_p
        return self._signal(book, ctx, market, spec, model_p)

    def _signal(
        self, book: OrderBook, ctx: Context, market: Market, spec: CryptoSpec, model_p: float
    ) -> list[Signal]:
        best_bid, best_ask = book.best_bid, book.best_ask
        if best_bid is None or best_ask is None:
            return []
        fee_rate = ctx.fee_rate(book.token_id)
        threshold = self.cfg.min_edge_cents / 100.0

        # Buy side: we post one tick inside the bid, so our fill price is the
        # price we quote, not the offer we would have lifted.
        buy_price = book.round_to_tick(min(best_bid + book.tick_size, best_ask - book.tick_size))
        buy_edge = model_p - buy_price
        sell_price = book.round_to_tick(max(best_ask - book.tick_size, best_bid + book.tick_size))
        sell_edge = sell_price - model_p

        position = ctx.portfolio.positions.get(book.token_id)
        held = position.qty if position else 0.0

        if buy_edge > threshold + fee_per_share(buy_price, fee_rate, ctx.fees.exponent):
            if self.structure_veto and self.structure_veto(spec.symbol, BUY):
                return []
            size = max(book.min_order_size / max(buy_price, 0.01), 0.0)
            return [
                Signal(
                    strategy=self.name, token_id=book.token_id, side=BUY, price=buy_price,
                    size=round(size, 2), edge=buy_edge, model_p=model_p, is_maker=True,
                    reason=f"model_{model_p:.3f}_vs_{buy_price:.3f}",
                )
            ]
        if held > 0 and sell_edge > threshold:
            if self.structure_veto and self.structure_veto(spec.symbol, SELL):
                return []
            return [
                Signal(
                    strategy=self.name, token_id=book.token_id, side=SELL, price=sell_price,
                    size=round(held, 2), edge=sell_edge, model_p=model_p, is_maker=True,
                    reason=f"model_{model_p:.3f}_vs_{sell_price:.3f}",
                )
            ]
        return []
