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

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pmbot.state import Portfolio
from pmbot.universe import Market

log = logging.getLogger(__name__)

@dataclass
class Redemption:
    token_id: str
    condition_id: str
    qty: float
    payout_per_share: float
    neg_risk: bool = False
    # Which outcome slot this token is, within its condition. The neg-risk
    # adapter redeems by per-outcome amounts rather than by index set.
    outcome_index: int = 0

    @property
    def payout(self) -> float:
        return self.qty * self.payout_per_share


Redeemer = Callable[[Redemption], Awaitable[bool]]


def token_index(raw_market: dict, token_id: str) -> int | None:
    """Position of a token within its condition's outcome list."""
    token_ids = raw_market.get("clobTokenIds")
    if isinstance(token_ids, str):
        token_ids = json.loads(token_ids)
    for index, candidate in enumerate(token_ids or []):
        if str(candidate) == token_id:
            return index
    return None


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
        min_payout_usd: float = 0.0,
    ):
        self.gamma = gamma
        self.portfolio = portfolio
        self.markets = markets
        self.mode = mode
        self.redeemer = redeemer
        self.alert = alert
        # Gas costs money; a dust redemption is not worth a transaction.
        self.min_payout_usd = min_payout_usd

    async def sweep(self) -> list[Redemption]:
        redemptions: list[Redemption] = []
        # One redeemPositions call settles every outcome of a condition, so a
        # condition is only ever touched once per sweep.
        settled_conditions: set[str] = set()
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
                neg_risk=bool(market.neg_risk),
                outcome_index=token_index(raw, token_id) or 0,
            )
            if redemption.payout > 0 and redemption.payout < self.min_payout_usd:
                log.info("skipping dust redemption of %.4f USDC", redemption.payout)
                continue
            on_chain = redemption.payout > 0 and market.condition_id not in settled_conditions
            if await self._settle(redemption, position, on_chain):
                if on_chain:
                    settled_conditions.add(market.condition_id)
                redemptions.append(redemption)
        return redemptions

    async def _settle(self, redemption: Redemption, position, on_chain: bool = True) -> bool:
        """Book the payout locally, sending the chain transaction first when
        there is actually something to claim.

        A losing token is worth nothing: it is cleared from the books without
        a transaction, and the winner's redeemPositions call settles the whole
        condition anyway.
        """
        if self.mode == "live" and on_chain:
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
            if not await self.redeemer(redemption):
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


class ChainRedeemer:
    """Redeems on Polygon through the CTF (or the neg-risk adapter).

    On a dry run it reports failure rather than success: nothing was claimed,
    so the position must stay on the books. That is the whole point of the
    dry run — you see what it would send, and the accounting does not move
    until it actually sends it.
    """

    def __init__(self, chain, dry_run: bool = True):
        self.chain = chain
        self.dry_run = dry_run

    async def __call__(self, redemption: Redemption) -> bool:
        amounts = None
        if redemption.neg_risk:
            from pmbot.chain import to_units

            # The adapter wants an amount per outcome slot; ours is the only
            # non-zero one.
            amounts = [0, 0]
            amounts[redemption.outcome_index] = to_units(redemption.qty)
        try:
            tx = await asyncio.to_thread(
                self.chain.redeem, redemption.condition_id, redemption.neg_risk,
                amounts, self.dry_run,
            )
        except Exception as exc:  # noqa: BLE001 - RPC and revert errors are a family
            log.error("redeem failed for %s: %s", redemption.condition_id[:12], exc)
            return False
        if self.dry_run:
            log.warning(
                "[dry-run] would redeem %.2f of %s for %.2f USDC — set chain.dry_run: false "
                "to actually claim it",
                redemption.qty, redemption.token_id[:12], redemption.payout,
            )
            return False
        return tx is not None


@dataclass
class MergedSet:
    condition_id: str
    shares: float
    cost_basis: float

    @property
    def proceeds(self) -> float:
        return self.shares  # a complete set is worth exactly $1


class SetMerger:
    """Turns complete sets back into USDC without waiting for resolution.

    An unwound arbitrage, or a maker filled on both sides of a binary market,
    leaves you holding one of each outcome. That set is worth exactly $1 at
    resolution and exactly $1 right now via mergePositions — so merging it
    frees the capital weeks early at the cost of one transaction.
    """

    def __init__(self, chain, portfolio: Portfolio, markets: dict[str, Market],
                 mode: str = "paper", dry_run: bool = True, min_shares: float = 5.0):
        self.chain = chain
        self.portfolio = portfolio
        self.markets = markets
        self.mode = mode
        self.dry_run = dry_run
        self.min_shares = min_shares

    def complete_sets(self) -> dict[str, tuple[list[str], float]]:
        """{condition_id: (token_ids, shares)} for every set we hold in full."""
        by_condition: dict[str, list[str]] = {}
        for token_id, market in self.markets.items():
            if market.condition_id:
                by_condition.setdefault(market.condition_id, []).append(token_id)

        out: dict[str, tuple[list[str], float]] = {}
        for condition_id, token_ids in by_condition.items():
            if len(token_ids) < 2:
                continue  # we only know the full outcome list for binary markets
            held = [self.portfolio.positions.get(t) for t in token_ids]
            if any(position is None or position.qty <= 0 for position in held):
                continue
            shares = min(position.qty for position in held)  # type: ignore[union-attr]
            if shares >= self.min_shares:
                out[condition_id] = (token_ids, shares)
        return out

    async def sweep(self) -> list[MergedSet]:
        merged: list[MergedSet] = []
        for condition_id, (token_ids, shares) in self.complete_sets().items():
            if self.mode == "live":
                if self.chain is None:
                    continue
                try:
                    tx = await asyncio.to_thread(
                        self.chain.merge, condition_id, shares, self.dry_run
                    )
                except Exception as exc:  # noqa: BLE001
                    log.error("merge failed for %s: %s", condition_id[:12], exc)
                    continue
                if self.dry_run or tx is None:
                    log.warning("[dry-run] would merge %.2f sets of %s for %.2f USDC",
                                shares, condition_id[:12], shares)
                    continue
            merged.append(self._book(condition_id, token_ids, shares))
        return merged

    def _book(self, condition_id: str, token_ids: list[str], shares: float) -> MergedSet:
        cost_basis = 0.0
        for token_id in token_ids:
            position = self.portfolio.position(token_id)
            cost_basis += position.avg_px * shares
            position.qty = max(0.0, position.qty - shares)
            if position.qty <= 1e-9:
                position.avg_px = 0.0
        realized = shares - cost_basis
        self.portfolio.free_usdc += shares
        self.portfolio.daily_realized_pnl += realized
        log.info("merged %.2f sets of %s for %.2f USDC (pnl %.2f)",
                 shares, condition_id[:12], shares, realized)
        return MergedSet(condition_id=condition_id, shares=shares, cost_basis=cost_basis)
