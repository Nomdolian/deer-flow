"""On-chain layer, tested against a fake web3 — no RPC, no key, no gas."""

import pytest

from pmbot.chain import (
    MAX_UINT256,
    ChainClient,
    Contracts,
    from_units,
    load_contracts,
    to_units,
)
from pmbot.execution.redeem import ChainRedeemer, Redemption, SetMerger, token_index
from pmbot.state import Portfolio
from tests.helpers import make_market

CONTRACTS = Contracts(
    collateral="0x" + "c0" * 20,
    conditional_tokens="0x" + "c7" * 20,
    exchange="0x" + "e1" * 20,
    neg_risk_exchange="0x" + "e2" * 20,
    neg_risk_adapter="0x" + "ad" * 20,
    exchange_v2="0x" + "e3" * 20,
    neg_risk_exchange_v2="0x" + "e4" * 20,
)


class FakeFunction:
    def __init__(self, recorder, name, args):
        self.recorder = recorder
        self.fn_name = name
        self.args = args

    def call(self):
        return self.recorder.returns.get(self.fn_name, 0)

    def build_transaction(self, tx):
        self.recorder.sent.append((self.fn_name, self.args))
        return {**tx, "data": "0x", "gas": 100_000}


class FakeContract:
    def __init__(self, recorder, address):
        self.recorder = recorder
        self.address = address
        self.functions = self

    def __getattr__(self, name):
        def make(*args):
            return FakeFunction(self.recorder, name, args)
        return make


class FakeEth:
    def __init__(self, recorder):
        self.recorder = recorder
        self.account = self

    def contract(self, address, abi):
        return FakeContract(self.recorder, address)

    def get_transaction_count(self, address):
        return 1

    def get_balance(self, address):
        return 10**18

    def sign_transaction(self, tx, key):
        return type("Signed", (), {"raw_transaction": b"\x01"})()

    def send_raw_transaction(self, raw):
        return type("H", (), {"hex": lambda self: "0xdeadbeef"})()

    def wait_for_transaction_receipt(self, tx_hash, timeout=0):
        return {"status": self.recorder.receipt_status}

    def from_key(self, key):
        return type("A", (), {"address": "0x" + "ab" * 20})()


class FakeWeb3:
    def __init__(self, returns=None, receipt_status=1):
        self.returns = returns or {}
        self.sent = []
        self.receipt_status = receipt_status
        self.eth = FakeEth(self)

    def to_checksum_address(self, address):
        return address

    def from_wei(self, value, unit):
        return value / 10**18


def client(**kwargs):
    fake = FakeWeb3(**kwargs)
    return ChainClient("http://rpc", "0x" + "11" * 32, CONTRACTS, w3=fake), fake


# ---- addresses and units --------------------------------------------------

def test_contract_addresses_come_from_the_sdk():
    contracts = load_contracts(137)
    assert contracts.exchange_v2 and contracts.conditional_tokens
    # V2 exchanges are approved first; the adapter is in the list for neg-risk.
    labels = [label for label, _ in contracts.spenders()]
    assert labels[0] == "exchange_v2" and "neg_risk_adapter" in labels


def test_bot_yaml_can_override_an_address():
    override = {"collateral": "0x" + "ff" * 20}
    assert load_contracts(137, override).collateral == "0x" + "ff" * 20


def test_empty_overrides_do_not_blank_an_address():
    assert load_contracts(137, {"collateral": ""}).collateral == load_contracts(137).collateral


def test_usdc_is_six_decimal_fixed_point():
    assert to_units(1.5) == 1_500_000
    assert from_units(1_500_000) == 1.5


# ---- allowances -----------------------------------------------------------

def test_allowance_status_reports_every_spender():
    chain, _ = client()
    rows = chain.allowance_status()
    assert len(rows) == len(CONTRACTS.spenders())
    assert all(row["usdc_allowance"] == 0 for row in rows)


def test_dry_run_sends_nothing():
    chain, fake = client()
    assert chain.ensure_allowances(dry_run=True) == []
    assert fake.sent == []


def test_approvals_cover_both_usdc_and_the_ctf():
    chain, fake = client()
    sent = chain.ensure_allowances(dry_run=False)
    names = [name for name, _ in fake.sent]
    assert names.count("approve") == len(CONTRACTS.spenders())
    assert names.count("setApprovalForAll") == len(CONTRACTS.spenders())
    assert len(sent) == len(fake.sent)
    # Approvals are unlimited so they are never topped up mid-session.
    assert fake.sent[0][1][1] == MAX_UINT256


def test_already_approved_spenders_are_skipped():
    chain, fake = client(returns={"allowance": MAX_UINT256, "isApprovedForAll": True})
    assert chain.ensure_allowances(dry_run=False) == []
    assert fake.sent == []


def test_a_reverted_transaction_raises():
    chain, _ = client(receipt_status=0)
    with pytest.raises(RuntimeError, match="reverted"):
        chain.ensure_allowances(dry_run=False)


# ---- settlement calls -----------------------------------------------------

def test_binary_redemption_goes_through_the_ctf():
    chain, fake = client()
    chain.redeem("0xcond", neg_risk=False, dry_run=False)
    name, args = fake.sent[0]
    assert name == "redeemPositions"
    assert args[0] == CONTRACTS.collateral and args[3] == [1, 2]


def test_neg_risk_redemption_goes_through_the_adapter():
    chain, fake = client()
    chain.redeem("0xcond", neg_risk=True, amounts=[5_000_000, 0], dry_run=False)
    name, args = fake.sent[0]
    # The adapter takes (conditionId, amounts) — no collateral, no index sets.
    assert name == "redeemPositions" and args == ("0xcond", [5_000_000, 0])


def test_merge_and_split_convert_between_usdc_and_sets():
    chain, fake = client()
    chain.merge("0xcond", 10.0, dry_run=False)
    chain.split("0xcond", 10.0, dry_run=False)
    assert [name for name, _ in fake.sent] == ["mergePositions", "splitPosition"]
    assert fake.sent[0][1][4] == to_units(10.0)


# ---- redeemer -------------------------------------------------------------

def redemption(neg_risk=False, index=0, qty=50.0, payout=1.0):
    return Redemption(token_id="tok", condition_id="0xcond", qty=qty,
                      payout_per_share=payout, neg_risk=neg_risk, outcome_index=index)


async def test_redeemer_dry_run_reports_failure_so_the_books_do_not_move():
    chain, fake = client()
    assert await ChainRedeemer(chain, dry_run=True)(redemption()) is False
    assert fake.sent == []


async def test_redeemer_sends_and_reports_success():
    chain, fake = client()
    assert await ChainRedeemer(chain, dry_run=False)(redemption()) is True
    assert fake.sent[0][0] == "redeemPositions"


async def test_redeemer_puts_the_amount_in_the_right_outcome_slot():
    chain, fake = client()
    await ChainRedeemer(chain, dry_run=False)(redemption(neg_risk=True, index=1, qty=25))
    assert fake.sent[0][1][1] == [0, to_units(25)]


async def test_redeemer_survives_a_reverted_transaction():
    chain, _ = client(receipt_status=0)
    assert await ChainRedeemer(chain, dry_run=False)(redemption()) is False


def test_token_index_locates_the_outcome_slot():
    raw = {"clobTokenIds": '["a","b"]'}
    assert token_index(raw, "b") == 1
    assert token_index(raw, "zzz") is None


# ---- set merging ----------------------------------------------------------

def set_portfolio(yes_qty=100.0, no_qty=100.0):
    portfolio = Portfolio(free_usdc=0.0)
    if yes_qty:
        portfolio.position("yes").apply_fill("BUY", 0.45, yes_qty)
    if no_qty:
        portfolio.position("no").apply_fill("BUY", 0.50, no_qty)
    markets = {
        "yes": make_market(token_id="yes", complement="no"),
        "no": make_market(token_id="no", complement="yes"),
    }
    return portfolio, markets


def test_a_complete_set_is_detected():
    portfolio, markets = set_portfolio()
    merger = SetMerger(None, portfolio, markets)
    assert merger.complete_sets() == {"cond": (["yes", "no"], 100.0)}


def test_holding_only_one_side_is_not_a_set():
    portfolio, markets = set_portfolio(no_qty=0)
    assert SetMerger(None, portfolio, markets).complete_sets() == {}


def test_set_size_is_the_smaller_leg():
    portfolio, markets = set_portfolio(yes_qty=100, no_qty=30)
    assert SetMerger(None, portfolio, markets).complete_sets()["cond"][1] == 30


def test_dust_sets_are_left_alone():
    portfolio, markets = set_portfolio(yes_qty=2, no_qty=2)
    assert SetMerger(None, portfolio, markets, min_shares=5).complete_sets() == {}


async def test_paper_merge_books_the_dollar_and_clears_both_legs():
    portfolio, markets = set_portfolio()
    merged = await SetMerger(None, portfolio, markets, mode="paper").sweep()
    assert len(merged) == 1
    # Bought the set for 0.95, merged at 1.00.
    assert portfolio.free_usdc == 100.0
    assert portfolio.position("yes").qty == 0 and portfolio.position("no").qty == 0
    assert portfolio.daily_realized_pnl == pytest.approx(5.0)


async def test_live_merge_sends_the_transaction():
    portfolio, markets = set_portfolio()
    chain, fake = client()
    merged = await SetMerger(chain, portfolio, markets, mode="live", dry_run=False).sweep()
    assert len(merged) == 1 and fake.sent[0][0] == "mergePositions"


async def test_live_dry_run_merge_changes_nothing():
    portfolio, markets = set_portfolio()
    chain, fake = client()
    assert await SetMerger(chain, portfolio, markets, mode="live", dry_run=True).sweep() == []
    assert fake.sent == [] and portfolio.position("yes").qty == 100.0
