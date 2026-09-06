import time

import pytest

from pmbot.backtest.metrics import compute, gate_for_live, max_drawdown_pct
from pmbot.execution.redeem import RedeemWorker, payout_for_token
from pmbot.state import Portfolio
from pmbot.store.db import Database
from pmbot.store.journal import Journal, TradeResult
from tests.helpers import make_market

# ---- journal --------------------------------------------------------------

def test_weights_never_zero_a_strategy_out():
    journal = Journal()
    now = time.time()
    for i in range(30):
        journal.record(TradeResult("s1_maker", pnl=1.0, ts=now - i * 3600))
        journal.record(TradeResult("s4_longshot", pnl=-1.0, ts=now - i * 3600))
    weights = journal.recompute_weights(now)
    assert weights["s1_maker"] > weights["s4_longshot"]
    assert weights["s4_longshot"] >= 0.1  # floored: losing the sample loses the strategy


def test_recent_results_count_for_more_than_old_ones():
    now = time.time()
    fresh, stale = Journal(), Journal()
    for i in range(30):
        fresh.record(TradeResult("s1_maker", pnl=2.0, ts=now - i * 3600))
        stale.record(TradeResult("s1_maker", pnl=2.0, ts=now - 120 * 86400 - i * 3600))
    assert fresh.stats["s1_maker"].decayed_score == stale.stats["s1_maker"].decayed_score
    fresh.recompute_weights(now)
    stale.recompute_weights(now)
    # Same trades, but the decayed expectancy of the old sample is far smaller.
    assert fresh.stats["s1_maker"].decayed_score > 0


def test_thin_samples_are_shrunk_toward_zero():
    now = time.time()
    thin, thick = Journal(), Journal()
    thin.record(TradeResult("s1_maker", pnl=10.0, ts=now))
    for _ in range(40):
        thick.record(TradeResult("s1_maker", pnl=10.0, ts=now))
    thin.recompute_weights(now)
    thick.recompute_weights(now)
    assert thin.stats["s1_maker"].decayed_score < thick.stats["s1_maker"].decayed_score


def test_consecutive_losses_counts_back_from_the_present():
    journal = Journal()
    now = time.time()
    journal.record(TradeResult("s1_maker", pnl=-1, ts=now - 30))
    journal.record(TradeResult("s1_maker", pnl=5, ts=now - 20))
    journal.record(TradeResult("s1_maker", pnl=-1, ts=now - 10))
    journal.record(TradeResult("s1_maker", pnl=-1, ts=now))
    assert journal.consecutive_losses() == 2


def test_edge_honesty_compares_realised_to_predicted():
    journal = Journal()
    journal.record(TradeResult("s3_fairvalue", pnl=1, ts=time.time(),
                               edge_predicted=0.05, edge_realised=0.02))
    assert journal.stats["s3_fairvalue"].edge_honesty == pytest.approx(0.4)  # the model is optimistic


# ---- metrics gate ---------------------------------------------------------

def test_max_drawdown():
    assert max_drawdown_pct([100, 120, 90, 130]) == 25.0


def test_live_gate_names_every_failure():
    metrics = compute([1.0, -2.0], [100, 101, 99])
    ok, failures = gate_for_live(metrics, daily_loss_kill_pct=3)
    assert not ok
    assert any("trades" in f for f in failures)
    assert any("expectancy" in f for f in failures)


def test_live_gate_passes_a_long_profitable_sample():
    pnls = [1.0] * 120
    curve = [100 + i for i in range(120)]
    ok, failures = gate_for_live(compute(pnls, curve), daily_loss_kill_pct=3)
    assert ok and not failures


# ---- redeem ---------------------------------------------------------------

def test_payout_is_none_until_the_market_closes():
    raw = {"closed": False, "clobTokenIds": '["a","b"]', "outcomePrices": '["1","0"]'}
    assert payout_for_token(raw, "a") is None


def test_payout_is_none_while_disputed():
    raw = {"closed": True, "umaResolutionStatus": "disputed",
           "clobTokenIds": '["a","b"]', "outcomePrices": '["1","0"]'}
    assert payout_for_token(raw, "a") is None


def test_payout_reads_the_winning_outcome():
    raw = {"closed": True, "clobTokenIds": '["a","b"]', "outcomePrices": '["1","0"]'}
    assert payout_for_token(raw, "a") == 1.0
    assert payout_for_token(raw, "b") == 0.0


class FakeGamma:
    def __init__(self, raw):
        self.raw = raw

    async def fetch_by_condition_id(self, condition_id):
        return self.raw


async def test_paper_sweep_credits_the_payout_and_clears_the_position():
    portfolio = Portfolio(free_usdc=100.0)
    portfolio.position("a").apply_fill("BUY", 0.40, 50)
    market = make_market(token_id="a")
    worker = RedeemWorker(
        FakeGamma({"closed": True, "clobTokenIds": '["a"]', "outcomePrices": '["1"]'}),
        portfolio, {"a": market},
    )
    redemptions = await worker.sweep()
    assert len(redemptions) == 1
    assert portfolio.free_usdc == 150.0  # 50 shares x $1
    assert portfolio.position("a").qty == 0
    assert portfolio.position("a").realized == 30.0  # bought at 0.40, redeemed at 1.00


async def test_live_sweep_without_a_redeemer_alerts_instead_of_pretending():
    alerts = []
    portfolio = Portfolio(free_usdc=0.0)
    portfolio.position("a").apply_fill("BUY", 0.40, 50)
    worker = RedeemWorker(
        FakeGamma({"closed": True, "clobTokenIds": '["a"]', "outcomePrices": '["1"]'}),
        portfolio, {"a": make_market(token_id="a")}, mode="live", alert=alerts.append,
    )
    assert await worker.sweep() == []
    assert portfolio.free_usdc == 0.0 and portfolio.position("a").qty == 50
    assert alerts and "manual redemption" in alerts[0]


# ---- persistence ----------------------------------------------------------

async def test_schema_round_trip(tmp_path):
    db = await Database(tmp_path / "t.sqlite").connect()
    await db.upsert_market(make_market())
    await db.insert_equity(100.0, 50.0)
    await db.upsert_strategy_stats("s1_maker", 10, 6, 12.5, 1.2)
    assert (await db.fetch_strategy_stats())[0]["weight"] == 1.2
    assert (await db.fetch_equity_curve())[0][1] == 150.0
    await db.close()


async def test_veto_reasons_are_queryable(tmp_path):
    from pmbot.strategies.base import Signal

    db = await Database(tmp_path / "t.sqlite").connect()
    for reason in ["stale_book", "stale_book", "edge_below_fee_floor"]:
        signal = Signal(strategy="s1_maker", token_id="tok", side="BUY", price=0.5, size=10)
        await db.insert_signal(signal, False, reason)
    counts = await db.fetch_veto_counts()
    assert counts["stale_book"] == 2 and counts["edge_below_fee_floor"] == 1
    await db.close()
