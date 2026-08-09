"""The demo round-trip verifier, including its safety rails.

This script is the only thing that proves orders actually reach the broker, so
its refusals matter as much as its happy path — a rail that silently doesn't
work would let someone fire a real order at funded money believing they were on
a demo account.
"""

import pytest

from scripts import verify_mt5_trade
from tests import fake_mt5

CRYPTO_CFD = {"trade_contract_size": 1.0, "digits": 2, "point": 0.01, "volume_min": 0.01, "volume_step": 0.01}


@pytest.fixture(autouse=True)
def _fake_mt5(monkeypatch):
    fake_mt5.reset()
    monkeypatch.setattr(verify_mt5_trade, "_import_mt5", lambda: fake_mt5)
    yield
    fake_mt5.reset()


def _run(monkeypatch, argv, stdin=None):
    monkeypatch.setattr("sys.argv", ["verify_mt5_trade", *argv])
    if stdin is not None:
        monkeypatch.setattr("builtins.input", lambda *_: stdin)
    verify_mt5_trade.main()


def test_round_trip_opens_and_closes_one_minimum_lot(monkeypatch, capsys):
    fake_mt5.add_symbol("EURUSD", 1.10)

    _run(monkeypatch, ["--symbol", "EURUSD", "--hold-seconds", "0"])

    out = capsys.readouterr().out
    assert "FILLED" in out
    assert "closed at" in out
    assert "works end to end" in out
    # Minimum lot, not a risk-derived size.
    assert fake_mt5.state.sent_volumes[0] == 0.01
    # And nothing is left open.
    assert fake_mt5.state.positions == {}


def test_order_carries_broker_side_stops_and_the_system_magic(monkeypatch):
    fake_mt5.add_symbol("EURUSD", 1.10)

    _run(monkeypatch, ["--symbol", "EURUSD", "--hold-seconds", "0"])

    opening = fake_mt5.state.sent_requests[0]
    assert opening["sl"] and opening["tp"], "stops must be attached at the broker, not held locally"
    assert opening["magic"] == verify_mt5_trade.SYSTEM_MAGIC


def test_refuses_to_trade_a_funded_account_without_the_override(monkeypatch):
    fake_mt5.add_symbol("EURUSD", 1.10)
    fake_mt5.state.account_trade_mode = fake_mt5.ACCOUNT_TRADE_MODE_REAL

    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, ["--symbol", "EURUSD"])

    assert "not a demo account" in str(exc.value)
    assert fake_mt5.state.sent_requests == [], "no order may be sent on a funded account by default"


def test_allow_live_still_requires_the_exact_confirmation_phrase(monkeypatch):
    fake_mt5.add_symbol("EURUSD", 1.10)
    fake_mt5.state.account_trade_mode = fake_mt5.ACCOUNT_TRADE_MODE_REAL

    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, ["--symbol", "EURUSD", "--allow-live"], stdin="yes")

    assert "Confirmation did not match" in str(exc.value)
    assert fake_mt5.state.sent_requests == []


def test_allow_live_proceeds_once_the_phrase_matches(monkeypatch):
    fake_mt5.add_symbol("EURUSD", 1.10)
    fake_mt5.state.account_trade_mode = fake_mt5.ACCOUNT_TRADE_MODE_REAL

    _run(
        monkeypatch,
        ["--symbol", "EURUSD", "--hold-seconds", "0", "--allow-live"],
        stdin=verify_mt5_trade.CONFIRM_PHRASE,
    )

    assert fake_mt5.state.sent_volumes[0] == 0.01


def test_refuses_when_algo_trading_is_disabled_in_the_terminal(monkeypatch):
    fake_mt5.add_symbol("EURUSD", 1.10)
    fake_mt5.state.algo_trading_allowed = False

    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, ["--symbol", "EURUSD"])

    assert "Algorithmic trading is DISABLED" in str(exc.value)


def test_closed_market_is_reported_as_not_a_fault(monkeypatch):
    fake_mt5.add_symbol("EURUSD", 1.10, tradeable=False)

    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, ["--symbol", "EURUSD"])

    assert "not tradeable right now" in str(exc.value)
    assert "not a fault" in str(exc.value)


def test_a_rejected_order_explains_the_likely_causes(monkeypatch):
    fake_mt5.add_symbol("EURUSD", 1.10)
    fake_mt5.state.reject_orders = True

    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, ["--symbol", "EURUSD"])

    message = str(exc.value)
    assert "ORDER REJECTED" in message
    assert "unsupported filling" in message


def test_a_failed_close_names_the_open_ticket(monkeypatch):
    """The worst outcome: a position opened that the system cannot close. It has
    to be impossible to miss, with the ticket to close by hand."""
    fake_mt5.add_symbol("EURUSD", 1.10)
    real_close = fake_mt5.order_send

    def _fail_on_close(request):
        if request.get("position"):
            return fake_mt5._OrderSendResult(retcode=fake_mt5.TRADE_RETCODE_REJECT, comment="close blocked")
        return real_close(request)

    monkeypatch.setattr(fake_mt5, "order_send", _fail_on_close)

    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, ["--symbol", "EURUSD", "--hold-seconds", "0"])

    message = str(exc.value)
    assert "CLOSE FAILED" in message
    assert "OPEN POSITION" in message
    assert "1000" in message  # the ticket, so it can be closed by hand


def test_works_on_a_crypto_cfd_contract(monkeypatch, capsys):
    fake_mt5.add_symbol("BTCUSD", 64_000.0, **CRYPTO_CFD)

    _run(monkeypatch, ["--symbol", "BTCUSD", "--hold-seconds", "0"])

    assert "works end to end" in capsys.readouterr().out
    assert fake_mt5.state.sent_volumes[0] == 0.01
