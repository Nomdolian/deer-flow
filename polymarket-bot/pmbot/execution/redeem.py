"""Settlement sweep.

Winning outcome tokens redeem 1:1 for USDC through the CTF contract once UMA
resolves — typically a couple of hours after the event, longer if disputed.
Nothing does this for you: unswept positions are dead capital sitting in a
resolved market.

In paper mode the sweep books the payout directly. In live mode redemption is
an on-chain call, so an explicit redeemer must be supplied; if none is, the
worker alerts rather than silently pretending the capital came back.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pmbot.state import Portfolio
from pmbot.universe import Market

log = logging.getLogger(__name__)

Redeemer = Callable[[str, str, float], Awaitable[bool]]  # condition_id, token_id, qty


@dataclass
class Redemption:
    token_id: str
    condition_id: str
    qty: float
    payout_per_share: float

    @property
    def payout(self) -> float:
        return self.qty * self.payout_per_share


def payout_for_token(raw_market: dict, token_id: str) -> float | None:
    """Resolved payout for one outcome token: 1.0 for the winner, 0.0 else.

    Returns None while the market is open or disputed — a disputed market can
    still flip, and redeeming into a dispute is not possible anyway.
    """
    if not raw_market.get("closed"):
        return None
    if str(raw_market.get("umaResolutionStatus", "")).lower() == "disputed":
        return None
    token_ids = raw_market.get("clobTokenIds")
    prices = raw_market.get("outcomePrices")
    if isinstance(token_ids, str):
        token_ids = json.loads(token_ids)
    if isinstance(prices, str):
        prices = json.loads(prices)
    if not token_ids or not prices or len(token_ids) != len(prices):
        return None
    for index, candidate in enumerate(token_ids):
        if str(candidate) == token_id:
            try:
                return float(prices[index])
            except (TypeError, ValueError):
                return None
    return None


class RedeemWorker:
    def __init__(
        self,
        gamma,
        portfolio: Portfolio,
        markets: dict[str, Market],
        mode: str = "paper",
        redeemer: Redeemer | None = None,
        alert: Callable[[str], None] | None = None,
    ):
        self.gamma = gamma
        self.portfolio = portfolio
        self.markets = markets
        self.mode = mode
        self.redeemer = redeemer
        self.alert = alert

    async def sweep(self) -> list[Redemption]:
        redemptions: list[Redemption] = []
        for token_id, position in list(self.portfolio.positions.items()):
            if position.qty <= 1e-9:
                continue
            market = self.markets.get(token_id)
            if market is None or not market.condition_id:
                continue
            raw = await self.gamma.fetch_by_condition_id(market.condition_id)
            if raw is None:
                continue
            payout = payout_for_token(raw, token_id)
            if payout is None:
                continue
            redemption = Redemption(
                token_id=token_id, condition_id=market.condition_id,
                qty=position.qty, payout_per_share=payout,
            )
            if await self._settle(redemption, position):
                redemptions.append(redemption)
        return redemptions

    async def _settle(self, redemption: Redemption, position) -> bool:
        if self.mode == "live":
            if self.redeemer is None:
                message = (
                    f"resolved position needs manual redemption: {redemption.qty:.2f} of "
                    f"{redemption.token_id[:12]} (condition {redemption.condition_id[:12]}), "
                    f"payout {redemption.payout:.2f} USDC"
                )
                log.warning(message)
                if self.alert:
                    self.alert(message)
                return False
            if not await self.redeemer(redemption.condition_id, redemption.token_id, redemption.qty):
                return False

        realized = redemption.qty * (redemption.payout_per_share - position.avg_px)
        position.realized += realized
        position.qty = 0.0
        position.avg_px = 0.0
        self.portfolio.free_usdc += redemption.payout
        self.portfolio.daily_realized_pnl += realized
        log.info(
            "redeemed %.2f of %s at %.2f (pnl %.2f)",
            redemption.qty, redemption.token_id[:12], redemption.payout_per_share, realized,
        )
        return True
