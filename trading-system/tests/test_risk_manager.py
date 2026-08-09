from datetime import UTC, datetime

from app.db.models import AssetClass, DecisionLog, Direction, StrategyVersion
from app.killswitch.service import is_engaged
from app.risk.manager import RiskManager, record_trade_result, resume_strategy
from app.risk.models import OpenPosition, PortfolioState
from app.signals.base import Signal


def make_signal(instrument="EURUSD", asset_class=AssetClass.forex, entry=1.1000, stop=1.0950, tp=1.1100, strategy_id="s1", version=1):
    return Signal(
        instrument=instrument,
        asset_class=asset_class,
        direction=Direction.long,
        confidence_score=0.8,
        entry=entry,
        stop_loss=stop,
        take_profit=tp,
        confluences=["test"],
        strategy_id=strategy_id,
        strategy_version=version,
        candle_time=datetime.now(UTC),
    )


def empty_portfolio(equity=10_000.0):
    return PortfolioState(
        equity=equity,
        open_positions=[],
        daily_realized_pnl=0.0,
        daily_starting_equity=equity,
        weekly_realized_pnl=0.0,
        weekly_starting_equity=equity,
    )


def test_position_sizing_scales_with_current_equity(db_session):
    manager = RiskManager(risk_per_trade_pct=0.01)
    signal = make_signal()
    result = manager.evaluate(db_session, signal, empty_portfolio(equity=10_000.0))
    assert result.accepted
    # risk_amount == equity * risk_pct; size == risk_amount / stop_distance
    assert result.risk_amount == 100.0
    stop_distance = abs(signal.entry - signal.stop_loss)
    assert abs(result.size - 100.0 / stop_distance) < 1e-9

    result2 = manager.evaluate(db_session, signal, empty_portfolio(equity=20_000.0))
    assert result2.risk_amount == 200.0  # doubled equity -> doubled risk amount, not fixed lot size


def test_portfolio_risk_cap_rejects_new_signal_over_ceiling(db_session):
    manager = RiskManager(risk_per_trade_pct=0.01, portfolio_risk_cap_pct=0.015)
    equity = 10_000.0
    existing = OpenPosition(instrument="GBPUSD", asset_class=AssetClass.forex, strategy_id="x", risk_amount=140.0, correlation_group="usd_majors")
    portfolio = PortfolioState(equity=equity, open_positions=[existing], daily_realized_pnl=0, daily_starting_equity=equity, weekly_realized_pnl=0, weekly_starting_equity=equity)

    signal = make_signal(instrument="XAUUSD")  # different correlation group so only the global cap is tested
    result = manager.evaluate(db_session, signal, portfolio)
    assert not result.accepted
    assert result.reason == "portfolio_risk_cap_exceeded"


def test_correlation_group_cap_rejects_even_under_global_cap(db_session):
    manager = RiskManager(risk_per_trade_pct=0.005, portfolio_risk_cap_pct=0.5, correlation_group_risk_cap_pct=0.01)
    equity = 10_000.0
    existing = OpenPosition(instrument="GBPUSD", asset_class=AssetClass.forex, strategy_id="x", risk_amount=90.0, correlation_group="usd_majors")
    portfolio = PortfolioState(equity=equity, open_positions=[existing], daily_realized_pnl=0, daily_starting_equity=equity, weekly_realized_pnl=0, weekly_starting_equity=equity)

    signal = make_signal(instrument="EURUSD")  # same correlation group (usd_majors)
    result = manager.evaluate(db_session, signal, portfolio)
    assert not result.accepted
    assert result.reason == "correlation_group_cap_exceeded"


def test_meme_bucket_isolated_from_core_portfolio_cap(db_session):
    manager = RiskManager(risk_per_trade_pct=0.01, portfolio_risk_cap_pct=0.001, meme_bucket_cap_pct=0.05)
    equity = 10_000.0
    # core portfolio cap is already effectively exhausted at 0.001 (=$10), but that must not block a meme trade
    portfolio = empty_portfolio(equity=equity)
    signal = make_signal(instrument="DOGEUSD", asset_class=AssetClass.crypto_meme)
    result = manager.evaluate(db_session, signal, portfolio)
    assert result.accepted
    assert result.correlation_group == "meme_bucket"


def test_meme_bucket_cap_enforced_independently(db_session):
    manager = RiskManager(risk_per_trade_pct=0.10, meme_bucket_cap_pct=0.05)
    equity = 10_000.0
    existing = OpenPosition(instrument="PEPEUSD", asset_class=AssetClass.crypto_meme, strategy_id="x", risk_amount=400.0, correlation_group="meme_bucket")
    portfolio = PortfolioState(equity=equity, open_positions=[existing], daily_realized_pnl=0, daily_starting_equity=equity, weekly_realized_pnl=0, weekly_starting_equity=equity)
    signal = make_signal(instrument="DOGEUSD", asset_class=AssetClass.crypto_meme)
    result = manager.evaluate(db_session, signal, portfolio)
    assert not result.accepted
    assert result.reason == "meme_bucket_cap_exceeded"


def test_daily_loss_limit_engages_kill_switch(db_session):
    manager = RiskManager(daily_loss_limit_pct=0.03)
    equity = 10_000.0
    portfolio = PortfolioState(
        equity=equity,
        open_positions=[],
        daily_realized_pnl=-350.0,  # -3.5% > 3% limit
        daily_starting_equity=equity,
        weekly_realized_pnl=0,
        weekly_starting_equity=equity,
    )
    signal = make_signal()
    result = manager.evaluate(db_session, signal, portfolio)
    assert not result.accepted
    assert result.reason == "daily_loss_limit_breached"
    assert is_engaged(db_session)


def test_consecutive_loss_breaker_pauses_strategy(db_session):
    strategy = StrategyVersion(strategy_id="s1", version=1, asset_class=AssetClass.forex)
    db_session.add(strategy)
    db_session.commit()

    for _ in range(4):
        record_trade_result(db_session, "s1", 1, is_loss=True, breaker_threshold=4)

    db_session.refresh(strategy)
    assert strategy.is_paused
    assert strategy.consecutive_losses == 4

    manager = RiskManager()
    signal = make_signal(strategy_id="s1", version=1)
    result = manager.evaluate(db_session, signal, empty_portfolio())
    assert not result.accepted
    assert result.reason == "strategy_paused"


def test_consecutive_loss_counter_resets_on_win(db_session):
    strategy = StrategyVersion(strategy_id="s2", version=1, asset_class=AssetClass.forex)
    db_session.add(strategy)
    db_session.commit()

    record_trade_result(db_session, "s2", 1, is_loss=True, breaker_threshold=4)
    record_trade_result(db_session, "s2", 1, is_loss=True, breaker_threshold=4)
    record_trade_result(db_session, "s2", 1, is_loss=False, breaker_threshold=4)

    db_session.refresh(strategy)
    assert strategy.consecutive_losses == 0
    assert not strategy.is_paused


def test_paused_strategy_never_unpauses_itself(db_session):
    """The regression this guards: the breaker latches, and a paused strategy
    takes no trades, so no win can ever arrive to clear it. Left unfixed, the
    system silently stops trading forever."""
    strategy = StrategyVersion(strategy_id="s3", version=1, asset_class=AssetClass.forex)
    db_session.add(strategy)
    db_session.commit()

    for _ in range(4):
        record_trade_result(db_session, "s3", 1, is_loss=True, breaker_threshold=4)
    db_session.refresh(strategy)
    assert strategy.is_paused

    # Even a win (however it arrived — a manual close, say) must not silently
    # re-arm the strategy behind the operator's back.
    record_trade_result(db_session, "s3", 1, is_loss=False, breaker_threshold=4)
    db_session.refresh(strategy)
    assert strategy.is_paused
    assert strategy.consecutive_losses == 0


def test_resume_strategy_clears_pause_and_counter(db_session):
    strategy = StrategyVersion(strategy_id="s4", version=1, asset_class=AssetClass.forex)
    db_session.add(strategy)
    db_session.commit()

    for _ in range(4):
        record_trade_result(db_session, "s4", 1, is_loss=True, breaker_threshold=4)
    db_session.refresh(strategy)
    assert strategy.is_paused

    resumed = resume_strategy(db_session, "s4", 1, resumed_by="test")
    assert resumed is not None
    assert not resumed.is_paused
    # Counter must reset too, or the next single loss re-trips the breaker.
    assert resumed.consecutive_losses == 0

    manager = RiskManager()
    result = manager.evaluate(db_session, make_signal(strategy_id="s4", version=1), empty_portfolio())
    assert result.accepted


def test_resume_strategy_is_logged_and_idempotent(db_session):
    strategy = StrategyVersion(strategy_id="s5", version=1, asset_class=AssetClass.forex)
    db_session.add(strategy)
    db_session.commit()

    resume_strategy(db_session, "s5", 1, resumed_by="operator")
    resume_strategy(db_session, "s5", 1, resumed_by="operator")

    events = db_session.query(DecisionLog).filter_by(event_type="strategy_resumed").all()
    assert len(events) == 2
    assert events[0].payload["resumed_by"] == "operator"
    # Resuming an already-active strategy is a no-op, recorded as such.
    assert events[1].payload["was_paused"] is False


def test_resume_unknown_strategy_returns_none(db_session):
    assert resume_strategy(db_session, "does-not-exist", 1, resumed_by="test") is None


def test_auto_pause_pushes_a_notification(db_session, monkeypatch):
    """A latching pause the operator isn't told about is indistinguishable from
    the system quietly dying."""
    sent: list[dict] = []
    monkeypatch.setattr(
        "app.risk.manager.notify_strategy_paused",
        lambda session, **kwargs: sent.append(kwargs),
    )

    strategy = StrategyVersion(strategy_id="s6", version=1, asset_class=AssetClass.forex)
    db_session.add(strategy)
    db_session.commit()

    for _ in range(4):
        record_trade_result(db_session, "s6", 1, is_loss=True, breaker_threshold=4)

    assert len(sent) == 1
    assert sent[0]["strategy_id"] == "s6"
    assert "consecutive losses" in sent[0]["reason"]

    # Further losses must not re-notify — the strategy is already paused.
    record_trade_result(db_session, "s6", 1, is_loss=True, breaker_threshold=4)
    assert len(sent) == 1
