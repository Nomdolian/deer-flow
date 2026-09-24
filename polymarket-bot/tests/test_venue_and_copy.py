"""Venue selection (international vs US) and the S5 wallet bars."""

import time

import httpx
import pytest

from pmbot.config import load_config
from pmbot.data.data_api import DataApiClient, WalletStats
from pmbot.execution.redeem import RedeemWorker
from pmbot.fees import DEFAULT_FEE_RATES, US_MAKER_REBATE, FeeTable
from pmbot.state import Portfolio
from tests.helpers import make_market

# ---- venue ----------------------------------------------------------------

def test_us_venue_rewrites_every_host(tmp_path):
    path = tmp_path / "bot.yaml"
    path.write_text("venue: us\n")
    cfg = load_config(path)
    assert cfg.host.endswith("polymarket.us")
    assert cfg.gamma_host.endswith("polymarket.us")
    assert cfg.ws_host.endswith("polymarket.us")
    assert cfg.geoblock_url.endswith("polymarket.us/api/geoblock")


def test_an_explicit_host_survives_venue_selection(tmp_path):
    path = tmp_path / "bot.yaml"
    path.write_text("venue: us\nhost: https://my-proxy.internal\n")
    assert load_config(path).host == "https://my-proxy.internal"


def test_international_is_the_default(tmp_path):
    assert load_config(tmp_path / "missing.yaml").host.endswith("polymarket.com")


def test_us_fee_schedule_is_flat_with_a_maker_rebate():
    table = FeeTable.for_venue("us")
    # No category arbitrage on the US venue: crypto costs the same as sport.
    assert table.rate_for("crypto") == table.rate_for("sports") == 0.05
    assert table.maker_rebate_rate == US_MAKER_REBATE
    assert table.maker_rebate(100, 0.5) == pytest.approx(100 * US_MAKER_REBATE * 0.25)


def test_international_keeps_the_per_category_schedule():
    table = FeeTable.for_venue("international")
    assert table.rate_for("geopolitics") == 0.0
    assert table.rate_for("crypto") == DEFAULT_FEE_RATES["crypto"]
    assert table.maker_rebate(100, 0.5) == 0.0  # discretionary, never modelled


# ---- wallet ranking -------------------------------------------------------

class FakeTransport(httpx.AsyncBaseTransport):
    def __init__(self, positions, trades=None):
        self.positions = positions
        self.trades = trades if trades is not None else [
            {"asset": "tok", "side": "BUY", "price": 0.5, "size": 10,
             "timestamp": time.time(), "transactionHash": "0x1"}
        ]

    async def handle_async_request(self, request):
        body = self.positions if "/positions" in str(request.url) else self.trades
        return httpx.Response(200, json=body)


def api(positions, trades=None):
    client = httpx.AsyncClient(transport=FakeTransport(positions, trades))
    return DataApiClient("https://data.example", client)


def closed(pnl):
    return {"realizedPnl": pnl, "size": 0, "currentValue": 0}


async def test_stats_count_only_closed_positions():
    stats = await api([closed(100), closed(-20), {"realizedPnl": 0, "size": 50,
                                                  "currentValue": 25}]).wallet_stats("0xw")
    assert stats.closed_positions == 2 and stats.wins == 1
    assert stats.realized_pnl == 80
    assert stats.open_exposure_usd == 25  # open risk is reported, not counted as record


async def test_score_discounts_pnl_by_hit_rate():
    lucky = WalletStats("0xa", realized_pnl=100_000, closed_positions=100, wins=10)
    steady = WalletStats("0xb", realized_pnl=60_000, closed_positions=100, wins=70)
    assert steady.score > lucky.score


async def test_ranking_drops_wallets_below_the_bars():
    thin = await api([closed(100_000)]).rank_wallets(["0xw"], min_pnl_usd=1000)
    assert thin == []  # one closed position is not a record


async def test_ranking_keeps_a_wallet_that_clears_every_bar():
    rows = [closed(1000)] * 30 + [closed(-100)] * 5
    ranked = await api(rows).rank_wallets(["0xw"], min_pnl_usd=1000)
    assert [s.wallet for s in ranked] == ["0xw"]
    assert ranked[0].hit_rate > 0.8


async def test_ranking_drops_an_idle_wallet():
    rows = [closed(1000)] * 30
    stale = [{"asset": "tok", "side": "BUY", "price": 0.5, "size": 10,
              "timestamp": time.time() - 200 * 86400, "transactionHash": "0x1"}]
    assert await api(rows, stale).rank_wallets(["0xw"], min_pnl_usd=1000) == []


async def test_ranking_drops_a_coin_flipper():
    rows = [closed(1000)] * 20 + [closed(-900)] * 20
    assert await api(rows).rank_wallets(["0xw"], min_pnl_usd=1000, min_hit_rate=0.6) == []


# ---- losing positions -----------------------------------------------------

class FakeGamma:
    def __init__(self, raw):
        self.raw = raw

    async def fetch_by_condition_id(self, condition_id):
        return self.raw


async def test_a_losing_position_is_cleared_without_a_transaction():
    # Redeeming a worthless token gains nothing and costs gas. The winner's
    # redeemPositions call settles the whole condition anyway.
    portfolio = Portfolio(free_usdc=0.0)
    portfolio.position("a").apply_fill("BUY", 0.40, 50)
    calls = []

    async def redeemer(redemption):
        calls.append(redemption)
        return True

    worker = RedeemWorker(
        FakeGamma({"closed": True, "clobTokenIds": '["a"]', "outcomePrices": '["0"]'}),
        portfolio, {"a": make_market(token_id="a")}, mode="live", redeemer=redeemer,
    )
    settled = await worker.sweep()
    assert len(settled) == 1 and calls == []
    assert portfolio.position("a").qty == 0
    assert portfolio.position("a").realized == -20.0  # the whole cost basis is gone


async def test_dust_payouts_are_left_for_later():
    portfolio = Portfolio(free_usdc=0.0)
    portfolio.position("a").apply_fill("BUY", 0.40, 0.5)
    worker = RedeemWorker(
        FakeGamma({"closed": True, "clobTokenIds": '["a"]', "outcomePrices": '["1"]'}),
        portfolio, {"a": make_market(token_id="a")}, mode="paper", min_payout_usd=1.0,
    )
    assert await worker.sweep() == []
    assert portfolio.position("a").qty == 0.5
