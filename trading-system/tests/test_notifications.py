from app.db.models import DeviceRecord
from app.killswitch.service import engage
from app.notifications.service import register_device, send_push, unregister_device


def test_register_device_is_idempotent_on_token(db_session):
    register_device(db_session, "token-a", "ios", label="phone1")
    register_device(db_session, "token-a", "ios", label="phone1-renamed")
    assert db_session.query(DeviceRecord).count() == 1
    assert db_session.query(DeviceRecord).first().label == "phone1-renamed"


def test_unregister_device_removes_row(db_session):
    register_device(db_session, "token-b", "android")
    unregister_device(db_session, "token-b")
    assert db_session.query(DeviceRecord).count() == 0


def test_send_push_with_no_devices_is_a_noop(db_session):
    send_push(db_session, title="t", body="b")  # must not raise even with zero registered devices


def test_send_push_network_failure_does_not_raise(db_session, monkeypatch):
    register_device(db_session, "token-c", "ios")

    class _BoomClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            raise RuntimeError("network down")

    monkeypatch.setattr("app.notifications.service.httpx.Client", lambda timeout: _BoomClient())
    send_push(db_session, title="t", body="b")  # best-effort: swallow, never raise into caller


def test_kill_switch_engage_does_not_raise_without_network(db_session, monkeypatch):
    register_device(db_session, "token-d", "ios")

    class _BoomClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            raise RuntimeError("network down")

    monkeypatch.setattr("app.notifications.service.httpx.Client", lambda timeout: _BoomClient())
    state = engage(db_session, reason="test", triggered_by="unit_test")
    assert state.engaged  # the safety-critical state change must succeed regardless of push delivery
