from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import AssetClass, RiskDecisionRecord, SignalRecord, StrategyVersion
from app.killswitch.service import engage as engage_kill_switch
from app.killswitch.service import is_engaged as kill_switch_engaged
from app.logging_utils import log_decision
from app.notifications.service import DAILY_LOSS_WARNING_RATIO, notify_daily_loss_approaching
from app.risk.correlation import correlation_group_for
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
    ):
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
            if daily_loss_pct >= self.daily_loss_limit_pct * DAILY_LOSS_WARNING_RATIO:
                notify_daily_loss_approaching(session, ratio_of_limit=daily_loss_pct / self.daily_loss_limit_pct)

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

        size_multiplier = strategy_version.size_multiplier if strategy_version else 1.0
        risk_amount = portfolio.equity * self.risk_per_trade_pct * size_multiplier
        size = risk_amount / stop_distance

        if signal.asset_class == AssetClass.crypto_meme:
            return self._evaluate_meme_bucket(portfolio, signal, size, risk_amount)

        return self._evaluate_core_portfolio(portfolio, signal, size, risk_amount)

    def _evaluate_meme_bucket(
        self, portfolio: PortfolioState, signal: Signal, size: float, risk_amount: float
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
        )

    def _evaluate_core_portfolio(
        self, portfolio: PortfolioState, signal: Signal, size: float, risk_amount: float
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
