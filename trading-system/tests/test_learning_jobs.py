from datetime import UTC, datetime, timedelta

from app.db.models import AssetClass, Direction, StrategyVersion, TradeJournalRecord
from app.learning.jobs import run_classification_sweep, run_mistake_report, run_weekly_review


class _FakeAnalyst:
    """Duck-typed stand-in for LLMAnalyst — no real Anthropic API calls."""

    def __init__(self, classification: str = "valid-setup-normal-variance", text: str = "no recurring pattern found"):
        self.classification = classification
        self.text = text
        self.json_calls = 0
        self.text_calls = 0

    def complete_json(self, *, system, user, max_tokens=200):
        self.json_calls += 1
        return {"classification": self.classification, "notes": "looked fine"}

    def complete_text(self, *, system, user, max_tokens=800):
        self.text_calls += 1
        return self.text


def _make_trade(
    session,
    *,
    strategy_id="smc_ict_structure",
    version=1,
    instrument="EURUSD",
    r_multiple=1.0,
    classification=None,
    closed=True,
):
    trade = TradeJournalRecord(
        order_id="order-1",
        strategy_id=strategy_id,
        strategy_version=version,
        instrument=instrument,
        asset_class=AssetClass.forex,
        direction=Direction.long,
        confluences=["always"],
        size=1000,
        entry_price=1.1,
        exit_price=1.11 if closed else None,
        stop_loss=1.09,
        take_profit=1.12,
        r_multiple=r_multiple if closed else None,
        pnl=10.0 if closed else None,
        outcome="win" if closed else None,
        classification=classification,
        opened_at=datetime.now(UTC),
        closed_at=datetime.now(UTC) if closed else None,
    )
    session.add(trade)
    session.commit()
    session.refresh(trade)
    return trade


def test_classification_sweep_only_processes_unclassified_closed_trades(db_session):
    _make_trade(db_session, classification=None)
    _make_trade(db_session, classification=None)
    _make_trade(db_session, classification="already-done")
    _make_trade(db_session, closed=False)  # still open — must not be touched

    analyst = _FakeAnalyst()
    count = run_classification_sweep(db_session, analyst)

    assert count == 2
    assert analyst.json_calls == 2
    classified = db_session.query(TradeJournalRecord).filter(TradeJournalRecord.classification == "valid-setup-normal-variance").count()
    assert classified == 2


def test_classification_sweep_respects_limit(db_session):
    for _ in range(5):
        _make_trade(db_session, classification=None)
    analyst = _FakeAnalyst()
    count = run_classification_sweep(db_session, analyst, limit=3)
    assert count == 3
    assert analyst.json_calls == 3


def test_weekly_review_skips_combinations_without_a_baseline(db_session):
    db_session.add(StrategyVersion(strategy_id="smc_ict_structure", version=1, asset_class=AssetClass.forex))
    db_session.commit()
    for _ in range(25):
        _make_trade(db_session, r_multiple=1.0)

    actions = run_weekly_review(db_session)
    assert len(actions) == 1
    assert "no_baseline" in actions[0]


def test_weekly_review_pauses_a_strategy_whose_live_edge_has_collapsed(db_session):
    db_session.add(
        StrategyVersion(
            strategy_id="smc_ict_structure",
            version=1,
            asset_class=AssetClass.forex,
            backtest_expectancy_r=1.0,
        )
    )
    db_session.commit()
    for _ in range(25):
        _make_trade(db_session, r_multiple=-0.5)  # live expectancy far below the 1.0R baseline

    actions = run_weekly_review(db_session)
    assert len(actions) == 1
    assert "paused_edge_degraded" in actions[0]

    strategy = db_session.query(StrategyVersion).filter_by(strategy_id="smc_ict_structure", version=1).one()
    assert strategy.is_paused


def test_weekly_review_pause_notifies_once(db_session, monkeypatch):
    """The edge-degradation pause latches like the consecutive-loss breaker, so
    it has to reach the operator's phone — and only on the transition."""
    sent: list[dict] = []
    monkeypatch.setattr(
        "app.journal.stats.notify_strategy_paused",
        lambda session, **kwargs: sent.append(kwargs),
    )
    db_session.add(
        StrategyVersion(
            strategy_id="smc_ict_structure",
            version=1,
            asset_class=AssetClass.forex,
            backtest_expectancy_r=1.0,
        )
    )
    db_session.commit()
    for _ in range(25):
        _make_trade(db_session, r_multiple=-0.5)

    run_weekly_review(db_session)
    assert len(sent) == 1
    assert "baseline" in sent[0]["reason"]

    run_weekly_review(db_session)  # already paused — no second alert
    assert len(sent) == 1


def test_mistake_report_with_no_trades_skips_the_llm_call(db_session):
    analyst = _FakeAnalyst()
    report = run_mistake_report(db_session, analyst)
    assert "No closed trades" in report
    assert analyst.text_calls == 0


def test_mistake_report_with_trades_calls_the_llm_and_logs_it(db_session):
    from app.db.models import DecisionLog

    _make_trade(db_session, classification="poor-entry-timing")
    analyst = _FakeAnalyst(text="losses cluster around Friday closes")
    report = run_mistake_report(db_session, analyst)

    assert report == "losses cluster around Friday closes"
    assert analyst.text_calls == 1
    logged = db_session.query(DecisionLog).filter_by(event_type="weekly_mistake_report").one()
    assert logged.payload["report"] == report


def test_mistake_report_respects_lookback_window(db_session):
    old_trade = _make_trade(db_session)
    old_trade.closed_at = datetime.now(UTC) - timedelta(days=30)
    db_session.commit()

    analyst = _FakeAnalyst()
    report = run_mistake_report(db_session, analyst, lookback_days=7)
    assert "No closed trades" in report
    assert analyst.text_calls == 0
