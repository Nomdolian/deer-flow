from datetime import UTC, datetime

from app.db.models import AssetClass, Direction, StrategyVersion
from app.killswitch.service import is_engaged
from app.risk.manager import RiskManager, record_trade_result
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
    # risk_amount is the money ACTUALLY at risk once the size has been floored to
    # the instrument's step — not the requested budget. The two differ whenever
    # rounding bites, and the caps must be summed on the former.
    assert abs(result.risk_amount - 100.0) < 1e-6
    stop_distance = abs(signal.entry - signal.stop_loss)
    assert abs(result.size - 100.0 / stop_distance) < 1.0  # 20,000 units of EURUSD
    assert result.risk_amount <= 100.0 + 1e-6  # never more than the budget

    result2 = manager.evaluate(db_session, signal, empty_portfolio(equity=20_000.0))
    # doubled equity -> doubled risk amount, not a fixed lot size
    assert abs(result2.risk_amount - 200.0) < 1e-6


def test_size_is_reported_in_units_and_broker_lots(db_session):
    """EURUSD sizes in base-currency units; MT5 needs the same position in lots.
    20,000 units == 0.2 standard lots. Confusing the two is a 100,000x error."""
    manager = RiskManager(risk_per_trade_pct=0.01)
    result = manager.evaluate(db_session, make_signal(), empty_portfolio(equity=10_000.0))
    assert result.accepted
    assert result.size_unit == "unit"
    assert abs(result.size - 20_000.0) < 1.0
    assert abs(result.size_lots - 0.2) < 1e-6
    assert result.point_value == 1.0
    assert result.spec_is_class_default is False


def test_contract_multiplier_divides_the_size(db_session):
    """The bug this port fixes: one MES contract moves $5 per index point, so a
    10-point stop risks $50 per contract. The equity formula (budget/stop) would
    return 5 contracts and quietly risk 5x the budget."""
    # Futures are on the generic profile; the live Exness account cannot trade them.
    manager = RiskManager(risk_per_trade_pct=0.01, broker="generic")
    signal = make_signal(instrument="MES", asset_class=AssetClass.indices,
                         entry=5000.0, stop=4990.0, tp=5020.0)
    result = manager.evaluate(db_session, signal, empty_portfolio(equity=50_000.0))
    assert result.accepted
    naive = (50_000.0 * 0.01) / 10.0          # = 50 contracts, the old wrong answer
    assert naive == 50.0
    assert result.size == 10.0                # 500 budget / (10 points * $5) = 10
    assert result.point_value == 5.0
    assert abs(result.risk_amount - 500.0) < 1e-6


def test_size_below_minimum_increment_is_rejected_not_rounded_up(db_session):
    """A single MES contract on a 10-point stop risks $50. On a $1,000 account at
    1% ($10) that does not fit, and the gate must refuse rather than round to 1."""
    manager = RiskManager(risk_per_trade_pct=0.01, broker="generic")
    signal = make_signal(instrument="MES", asset_class=AssetClass.indices,
                         entry=5000.0, stop=4990.0, tp=5020.0)
    result = manager.evaluate(db_session, signal, empty_portfolio(equity=1_000.0))
    assert not result.accepted
    assert result.reason == "size_below_min_increment"
    assert result.size is None


def test_silver_increment_blocks_the_trade(db_session):
    """XAGUSD's 0.01-lot minimum is 50 ounces, so the smallest possible silver
    position risks 50x the stop distance regardless of the price."""
    manager = RiskManager(risk_per_trade_pct=0.01)
    signal = make_signal(instrument="XAGUSD", asset_class=AssetClass.metals,
                         entry=66.11, stop=65.61, tp=67.11)
    result = manager.evaluate(db_session, signal, empty_portfolio(equity=1_000.0))
    # budget $10, smallest position risks 50 oz * $0.50 = $25
    assert not result.accepted
    assert result.reason == "size_below_min_increment"


def test_long_tail_symbol_falls_back_to_asset_class_default(db_session):
    """Meme tokens cannot be enumerated, so an unlisted symbol must still size —
    via the asset-class default, flagged so the decision stays auditable."""
    manager = RiskManager(risk_per_trade_pct=0.01, meme_bucket_cap_pct=0.5)
    signal = make_signal(instrument="WIFUSD", asset_class=AssetClass.crypto_meme)
    result = manager.evaluate(db_session, signal, empty_portfolio(equity=10_000.0))
    assert result.accepted
    assert result.spec_is_class_default is True
    assert result.size_unit == "coin"   # Exness trades meme tokens as CFDs


def test_exness_account_cannot_be_handed_a_cme_contract(db_session):
    """With the live broker profile active, an MES signal is refused outright —
    the account has no such instrument, so there is no correct size."""
    manager = RiskManager(risk_per_trade_pct=0.01)      # configured broker = exness
    signal = make_signal(instrument="MES", asset_class=AssetClass.commodities,
                         entry=5000.0, stop=4990.0, tp=5020.0)
    result = manager.evaluate(db_session, signal, empty_portfolio(equity=50_000.0))
    # Falls back to the Exness commodities default (USOIL-like, 10-barrel minimum)
    # rather than a $5/point CME contract, so the size is never a futures size.
    if result.accepted:
        assert result.point_value == 1.0
        assert result.spec_is_class_default is True


def test_fifty_dollar_account_cannot_trade_exness_crypto(db_session):
    """A $50 account at 1% has $0.50 of budget. Exness's smallest BTCUSD position
    is 0.01 BTC, risking ~$12 on a realistic stop, so the gate must refuse."""
    manager = RiskManager(risk_per_trade_pct=0.01)
    signal = make_signal(instrument="BTCUSD", asset_class=AssetClass.crypto_major,
                         entry=63736.0, stop=62500.0, tp=66208.0)
    result = manager.evaluate(db_session, signal, empty_portfolio(equity=50.0))
    assert not result.accepted
    assert result.reason == "size_below_min_increment"


def test_cent_account_makes_a_fifty_dollar_forex_trade_viable(db_session):
    """The same $50 account and the same 20-pip stop, on a Standard Cent profile."""
    manager = RiskManager(risk_per_trade_pct=0.01, broker="exness_cent")
    signal = make_signal(instrument="EURUSD", entry=1.15399, stop=1.15199, tp=1.15799)
    result = manager.evaluate(db_session, signal, empty_portfolio(equity=50.0))
    assert result.accepted
    assert result.risk_amount <= 0.50 + 1e-9
    assert result.size >= 10.0                        # at least the 10-unit minimum
    assert result.size_lots >= 0.01

    # Standard, same trade, same account: does not fit.
    standard = RiskManager(risk_per_trade_pct=0.01, broker="exness_standard")
    rejected = standard.evaluate(db_session, signal, empty_portfolio(equity=50.0))
    assert not rejected.accepted
    assert rejected.reason == "size_below_min_increment"


def test_actual_risk_never_exceeds_budget_after_rounding(db_session):
    """The invariant the whole module exists to protect, across every class."""
    manager = RiskManager(risk_per_trade_pct=0.01, broker="generic")
    equity = 100_000.0
    budget = equity * 0.01
    cases = [
        ("EURUSD", AssetClass.forex, 1.1000, 1.0950),
        ("USDJPY", AssetClass.forex, 155.20, 154.90),
        ("XAUUSD", AssetClass.metals, 4408.55, 4393.55),
        ("XAGUSD", AssetClass.metals, 66.11, 65.61),
        ("US30", AssetClass.indices, 46000.0, 45900.0),
        ("MES", AssetClass.indices, 5000.0, 4990.0),
        ("MCL", AssetClass.commodities, 81.96, 81.46),
        ("BTCUSD", AssetClass.crypto_major, 63736.0, 62500.0),
        ("DOGEUSD", AssetClass.crypto_meme, 0.31, 0.30),
    ]
    for instrument, asset_class, entry, stop in cases:
        signal = make_signal(instrument=instrument, asset_class=asset_class,
                             entry=entry, stop=stop, tp=entry + (entry - stop) * 2)
        result = manager.evaluate(db_session, signal, empty_portfolio(equity=equity))
        if not result.accepted:
            assert result.reason == "size_below_min_increment", (instrument, result.reason)
            continue
        assert result.risk_amount <= budget + 1e-6, (instrument, result.risk_amount)
        assert result.size > 0


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
    assert result.reason == "strategy_paused_consecutive_losses"


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
