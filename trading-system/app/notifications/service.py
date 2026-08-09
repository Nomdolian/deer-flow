from datetime import UTC, datetime

import httpx
from sqlalchemy.orm import Session

from app.db.models import DecisionLog, DeviceRecord
from app.logging_utils import log_decision

_EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"

# Phase 8, item 3: mobile push for new high-confidence signal, position
# opened/closed, daily loss limit approaching, kill switch triggered, feed
# health issue.
SIGNAL_CONFIDENCE_PUSH_THRESHOLD = 0.75
DAILY_LOSS_WARNING_RATIO = 0.8  # notify once daily loss reaches 80% of the hard limit


def register_device(session: Session, push_token: str, platform: str, label: str | None = None) -> DeviceRecord:
    device = session.query(DeviceRecord).filter_by(push_token=push_token).one_or_none()
    if device is None:
        device = DeviceRecord(push_token=push_token, platform=platform, label=label)
        session.add(device)
    else:
        device.platform = platform
        if label:
            device.label = label
        device.last_seen_at = datetime.now(UTC)
    session.commit()
    session.refresh(device)
    return device


def unregister_device(session: Session, push_token: str) -> None:
    session.query(DeviceRecord).filter_by(push_token=push_token).delete()
    session.commit()


def send_push(session: Session, *, title: str, body: str, data: dict | None = None) -> None:
    """Best-effort fan-out to every registered device via Expo's push API.
    Never raises into the caller — this is a notification/control surface, not
    part of the execution path, so a delivery failure must never affect
    trading logic (non-negotiable constraint: nothing outside app/execution
    touches order placement, and notifications must not be able to block it
    either)."""
    try:
        tokens = [d.push_token for d in session.query(DeviceRecord).all()]
        if not tokens:
            return
        messages = [{"to": t, "title": title, "body": body, "data": data or {}, "sound": "default"} for t in tokens]
        with httpx.Client(timeout=10.0) as client:
            client.post(_EXPO_PUSH_URL, json=messages, headers={"Content-Type": "application/json"})
    except Exception as exc:
        log_decision(session, event_type="push_notification_failed", agent_id="notifications", payload={"error": str(exc)})


def _already_notified_today(session: Session, event_type: str, instrument: str | None = None) -> bool:
    since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    query = session.query(DecisionLog).filter(DecisionLog.event_type == event_type, DecisionLog.created_at >= since)
    if instrument is not None:
        query = query.filter(DecisionLog.instrument == instrument)
    return query.first() is not None


def notify_signal(session: Session, signal) -> None:
    if signal.confidence_score < SIGNAL_CONFIDENCE_PUSH_THRESHOLD:
        return
    send_push(
        session,
        title=f"New signal: {signal.instrument}",
        body=f"{signal.direction.value.upper()} via {signal.strategy_id} — confidence {signal.confidence_score:.0%}",
        data={"type": "signal", "instrument": signal.instrument},
    )


def notify_position_opened(session: Session, order_record) -> None:
    send_push(
        session,
        title="Position opened",
        body=f"{order_record.direction.value.upper()} {order_record.instrument} @ {order_record.filled_price}",
        data={"type": "position_opened", "instrument": order_record.instrument},
    )


def notify_position_closed(session: Session, trade) -> None:
    pnl = trade.pnl or 0.0
    send_push(
        session,
        title="Position closed",
        body=f"{trade.instrument} {trade.outcome} ({pnl:+.2f}, {trade.r_multiple:+.2f}R)" if trade.r_multiple is not None else f"{trade.instrument} {trade.outcome}",
        data={"type": "position_closed", "instrument": trade.instrument},
    )


def notify_daily_loss_approaching(session: Session, *, ratio_of_limit: float) -> None:
    if _already_notified_today(session, "daily_loss_warning_pushed"):
        return
    send_push(
        session,
        title="Daily loss limit approaching",
        body=f"{ratio_of_limit:.0%} of today's loss limit used",
        data={"type": "daily_loss_warning"},
    )
    log_decision(session, event_type="daily_loss_warning_pushed", agent_id="notifications", payload={"ratio_of_limit": ratio_of_limit})


def notify_kill_switch_triggered(session: Session, *, reason: str, triggered_by: str) -> None:
    send_push(
        session,
        title="Kill switch engaged",
        body=f"{reason} (by {triggered_by})",
        data={"type": "kill_switch"},
    )


def notify_strategy_paused(session: Session, *, strategy_id: str, version: int, reason: str) -> None:
    """A paused strategy stops taking trades and does NOT un-pause itself, so
    this notification is the only thing standing between "the breaker did its
    job" and "the bot went quiet three weeks ago and nobody noticed"."""
    send_push(
        session,
        title="Strategy paused",
        body=f"{strategy_id} v{version} paused: {reason}. It will not take new trades until you resume it.",
        data={"type": "strategy_paused", "strategy_id": strategy_id, "version": version, "reason": reason},
    )


def notify_feed_health_issue(session: Session, *, instrument: str) -> None:
    if _already_notified_today(session, "feed_stale_pushed", instrument=instrument):
        return
    send_push(
        session,
        title="Feed health issue",
        body=f"{instrument} feed is stale — new signal generation paused for it",
        data={"type": "feed_health", "instrument": instrument},
    )
    log_decision(session, event_type="feed_stale_pushed", agent_id="notifications", instrument=instrument, payload={})
