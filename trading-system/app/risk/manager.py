from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import AssetClass, RiskDecisionRecord, SignalRecord, StrategyVersion
from app.killswitch.service import engage as engage_kill_switch
from app.killswitch.service import is_engaged as kill_switch_engaged
from app.logging_utils import log_decision
from app.risk.correlation import correlation_group_for
from app.risk.instruments import (
    quote_conversion,
    risk_for_size,
    size_for_risk,
    spec_for,
    units_to_lots,
)
from app.risk.models import PortfolioState, RiskCheckResult
from app.signals.base import Signal


class RiskManager:
    """The single authoritative risk gate. No agent, engine, or execution path
    may bypass it (Phase 3 preamble). Every call is logged, accepted or
    rejected, with the reason — this IS the mechanical learning loop's input,
    not narrative learning.
    """

    def __init__(
        self,
        risk_per_trade_pct: float | None = None,
        portfolio_risk_cap_pct: float | None = None,
        correlation_group_risk_cap_pct: float | None = None,
        meme_bucket_cap_pct: float | None = None,
        daily_loss_limit_pct: float | None = None,
        weekly_loss_limit_pct: float | None = None,
        broker: str | None = None,
        account_currency: str = "USD",
    ):
        # None means "use the configured broker profile". Pass explicitly to size
        # against a different broker's contract specs (e.g. backtesting exchange
        # futures data while the live account is a CFD account).
        self.broker = broker
        # Drives quote-currency conversion: risk figures are only dollars when the
        # instrument's quote currency matches this.
        self.account_currency = account_currency
        self.risk_per_trade_pct = risk_per_trade_pct or settings.risk_per_trade_pct
        self.portfolio_risk_cap_pct = portfolio_risk_cap_pct or settings.portfolio_risk_cap_pct
        self.correlation_group_risk_cap_pct = correlation_group_risk_cap_pct or settings.correlation_group_risk_cap_pct
        self.meme_bucket_cap_pct = meme_bucket_cap_pct or settings.meme_bucket_cap_pct
        self.daily_loss_limit_pct = daily_loss_limit_pct or settings.daily_loss_limit_pct
        self.weekly_loss_limit_pct = weekly_loss_limit_pct or settings.weekly_loss_limit_pct

    def evaluate(self, session: Session, signal: Signal, portfolio: PortfolioState) -> RiskCheckResult:
        signal_record = self._persist_signal(session, signal)
        result = self._evaluate_internal(session, signal, portfolio)
        self._log(session, signal_record.id, result)
        return result

    def _evaluate_internal(self, session: Session, signal: Signal, portfolio: PortfolioState) -> RiskCheckResult:
        if kill_switch_engaged(session):
            return RiskCheckResult(accepted=False, reason="kill_switch_engaged")

        strategy_version = self._get_strategy_version(session, signal)
        if strategy_version is not None and strategy_version.is_paused:
            return RiskCheckResult(accepted=False, reason="strategy_paused_consecutive_losses")

        if portfolio.daily_starting_equity > 0:
            daily_loss_pct = -portfolio.daily_realized_pnl / portfolio.daily_starting_equity
            if daily_loss_pct >= self.daily_loss_limit_pct:
                engage_kill_switch(session, reason="daily_loss_limit_breached", triggered_by="risk_manager")
                return RiskCheckResult(accepted=False, reason="daily_loss_limit_breached")

        if portfolio.weekly_starting_equity > 0:
            weekly_loss_pct = -portfolio.weekly_realized_pnl / portfolio.weekly_starting_equity
            if weekly_loss_pct >= self.weekly_loss_limit_pct:
                engage_kill_switch(session, reason="weekly_loss_limit_breached", triggered_by="risk_manager")
                return RiskCheckResult(accepted=False, reason="weekly_loss_limit_breached")

        stop_distance = abs(signal.entry - signal.stop_loss)
        if stop_distance <= 0:
            return RiskCheckResult(accepted=False, reason="invalid_stop_distance")

        if portfolio.equity <= 0:
            return RiskCheckResult(accepted=False, reason="invalid_equity")

        # The contract specification, not the bare stop distance, determines size.
        # An MES contract moves $5 per index point, so `risk_budget / stop_distance`
        # would return 5x too many contracts; and broker size steps mean a raw
        # fractional size is not a placeable order. See app/risk/instruments.py.
        spec = spec_for(signal.instrument, signal.asset_class, self.broker)
        if spec is None:
            # Fail closed. Either the broker does not offer this asset class (asking
            # a CFD broker for a CME contract, or a Cent account for crypto) or the
            # class is unrecognised. Guessing would place a real order at a size the
            # account cannot support.
            return RiskCheckResult(accepted=False, reason="no_instrument_spec")

        # A price movement is only money when the quote currency is the account
        # currency. A 0.30 move on USDJPY is 0.30 JPY (~$0.0019), not $0.30 —
        # sizing without converting overstates risk ~155x on every JPY-quoted pair.
        quote_rate = quote_conversion(spec, signal.entry, self.account_currency)
        if quote_rate is None:
            # A cross needs a third rate this pair's own price cannot supply. Fail
            # closed: undersizing by the rate is still the wrong size.
            return RiskCheckResult(accepted=False, reason="quote_conversion_unavailable")

        size_multiplier = strategy_version.size_multiplier if strategy_version else 1.0
        risk_budget = portfolio.equity * self.risk_per_trade_pct * size_multiplier
        size = size_for_risk(risk_budget, stop_distance, spec, quote_rate)
        if size <= 0:
            # The smallest tradeable position risks more than the budget allows.
            # This is a legitimate "no trade", not an error to round away.
            return RiskCheckResult(accepted=False, reason="size_below_min_increment")

        # Flooring to the size step leaves actual risk at or below the budget. The
        # caps must be summed on what is really at risk, not on what was requested.
        risk_amount = risk_for_size(stop_distance, size, spec, quote_rate)

        if signal.asset_class == AssetClass.crypto_meme:
            return self._evaluate_meme_bucket(portfolio, signal, size, risk_amount, spec)

        return self._evaluate_core_portfolio(portfolio, signal, size, risk_amount, spec)

    def _evaluate_meme_bucket(
        self, portfolio: PortfolioState, signal: Signal, size: float, risk_amount: float, spec
    ) -> RiskCheckResult:
        # Isolated bucket: losses here cannot cascade into core position sizing,
        # and core-portfolio exposure never blocks a meme trade or vice versa (Phase 3, item 4).
        meme_open_risk = sum(p.risk_amount for p in portfolio.open_positions if p.asset_class == AssetClass.crypto_meme)
        cap = portfolio.equity * self.meme_bucket_cap_pct
        if meme_open_risk + risk_amount > cap:
            return RiskCheckResult(accepted=False, reason="meme_bucket_cap_exceeded")
        return RiskCheckResult(
            accepted=True,
            reason="accepted",
            size=size,
            risk_amount=risk_amount,
            portfolio_risk_after_pct=(meme_open_risk + risk_amount) / portfolio.equity,
            correlation_group="meme_bucket",
            **self._spec_fields(size, spec),
        )

    @staticmethod
    def _spec_fields(size: float, spec) -> dict:
        return {
            "size_unit": spec.unit,
            "size_lots": units_to_lots(size, spec),
            "point_value": spec.point_value,
            "spec_is_class_default": spec.is_class_default,
        }

    def _evaluate_core_portfolio(
        self, portfolio: PortfolioState, signal: Signal, size: float, risk_amount: float, spec
    ) -> RiskCheckResult:
        core_positions = [p for p in portfolio.open_positions if p.asset_class != AssetClass.crypto_meme]
        core_open_risk = sum(p.risk_amount for p in core_positions)
        portfolio_cap = portfolio.equity * self.portfolio_risk_cap_pct
        if core_open_risk + risk_amount > portfolio_cap:
            return RiskCheckResult(accepted=False, reason="portfolio_risk_cap_exceeded")

        group = correlation_group_for(signal.instrument)
        group_open_risk = sum(p.risk_amount for p in core_positions if p.correlation_group == group)
        group_cap = portfolio.equity * self.correlation_group_risk_cap_pct
        if group_open_risk + risk_amount > group_cap:
            return RiskCheckResult(accepted=False, reason="correlation_group_cap_exceeded", correlation_group=group)

        return RiskCheckResult(
            accepted=True,
            reason="accepted",
            size=size,
            risk_amount=risk_amount,
            portfolio_risk_after_pct=(core_open_risk + risk_amount) / portfolio.equity,
            correlation_group=group,
            **self._spec_fields(size, spec),
        )

    def _get_strategy_version(self, session: Session, signal: Signal) -> StrategyVersion | None:
        return (
            session.query(StrategyVersion)
            .filter_by(strategy_id=signal.strategy_id, version=signal.strategy_version)
            .one_or_none()
        )

    def _persist_signal(self, session: Session, signal: Signal) -> SignalRecord:
        record = SignalRecord(
            strategy_id=signal.strategy_id,
            strategy_version=signal.strategy_version,
            instrument=signal.instrument,
            asset_class=signal.asset_class,
            direction=signal.direction,
            confidence_score=signal.confidence_score,
            entry=signal.entry,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            confluences=signal.confluences,
            candle_time=signal.candle_time,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return record

    def _log(self, session: Session, signal_id: str, result: RiskCheckResult) -> None:
        session.add(
            RiskDecisionRecord(
                signal_id=signal_id,
                accepted=result.accepted,
                reason=result.reason,
                approved_size=result.size,
                risk_amount=result.risk_amount,
                portfolio_risk_after_pct=result.portfolio_risk_after_pct,
                correlation_group=result.correlation_group,
            )
        )
        session.commit()
        log_decision(
            session,
            event_type="risk_check",
            agent_id="risk_manager",
            payload={"signal_id": signal_id, "accepted": result.accepted, "reason": result.reason},
        )


def record_trade_result(session: Session, strategy_id: str, version: int, *, is_loss: bool, breaker_threshold: int | None = None) -> StrategyVersion | None:
    """Mechanical, hard-coded consecutive-loss circuit breaker (Phase 3, item 6).
    Called by the journal service when a trade closes — not by the LLM layer."""
    threshold = breaker_threshold or settings.consecutive_loss_breaker
    strategy_version = (
        session.query(StrategyVersion).filter_by(strategy_id=strategy_id, version=version).one_or_none()
    )
    if strategy_version is None:
        return None

    if is_loss:
        strategy_version.consecutive_losses += 1
        if strategy_version.consecutive_losses >= threshold and not strategy_version.is_paused:
            strategy_version.is_paused = True
            log_decision(
                session,
                event_type="strategy_auto_paused",
                agent_id="risk_manager",
                payload={
                    "strategy_id": strategy_id,
                    "version": version,
                    "consecutive_losses": strategy_version.consecutive_losses,
                },
            )
    else:
        strategy_version.consecutive_losses = 0

    session.commit()
    return strategy_version
