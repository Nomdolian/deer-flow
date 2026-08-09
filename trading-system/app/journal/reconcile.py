from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.db.models import OrderRecord
from app.execution.base import ExecutionAdapter
from app.journal.service import list_open_trades, record_trade_close
from app.logging_utils import log_decision


@dataclass
class ReconcileReport:
    closed_while_offline: list[str] = field(default_factory=list)
    still_open: list[str] = field(default_factory=list)
    untracked_at_broker: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"closed_while_offline={len(self.closed_while_offline)} "
            f"still_open={len(self.still_open)} "
            f"untracked_at_broker={len(self.untracked_at_broker)} "
            f"unresolved={len(self.unresolved)}"
        )


def reconcile_positions(session: Session, execution: ExecutionAdapter) -> ReconcileReport:
    """Reconcile the DB's view of open trades against the broker's actual
    positions, at startup.

    This is what makes restarts safe. A position's stop-loss and take-profit
    live broker-side, so while this process was down the broker may well have
    closed a trade — the DB still thinks it's open. Without reconciliation the
    journal would carry a permanently-open phantom trade, the risk manager
    would keep counting its risk against the portfolio cap forever, and the
    strategy's performance stats would never see the outcome.

    Deliberately conservative in both directions:
    - a trade the DB thinks is open but the broker doesn't have is journaled
      as closed, using the broker's actual exit price where we can get it
    - a broker position we can't match to a DB trade is REPORTED, never
      adopted or closed. It may be a manual trade of yours; guessing would
      risk interfering with a position the system never opened.
    """
    report = ReconcileReport()

    try:
        broker_positions = execution.get_open_positions()
    except Exception as exc:  # noqa: BLE001 - reconciliation must not prevent startup
        log_decision(
            session,
            event_type="reconcile_failed",
            agent_id="reconciler",
            payload={"error": str(exc)},
        )
        report.unresolved.append(f"could not read broker positions: {exc}")
        return report

    broker_by_position_id = {p.broker_position_id: p for p in broker_positions if p.broker_position_id}
    broker_by_client_id = {p.client_order_id: p for p in broker_positions if p.client_order_id}
    matched_broker_ids: set[str] = set()

    for trade in list_open_trades(session):
        order = session.get(OrderRecord, trade.order_id)
        if order is None:
            report.unresolved.append(trade.id)
            continue

        position = None
        if order.broker_position_id and order.broker_position_id in broker_by_position_id:
            position = broker_by_position_id[order.broker_position_id]
        elif order.client_order_id in broker_by_client_id:
            position = broker_by_client_id[order.client_order_id]

        if position is not None:
            matched_broker_ids.add(position.broker_position_id or position.client_order_id)
            report.still_open.append(trade.id)
            continue

        # Gone from the broker: it closed while we were down.
        exit_price = _resolve_offline_exit_price(execution, order.broker_position_id)
        if exit_price is None:
            # Never invent a fill price — an unverifiable PnL would silently
            # corrupt the very stats the learning loop depends on.
            report.unresolved.append(trade.id)
            log_decision(
                session,
                event_type="reconcile_unresolved_close",
                agent_id="reconciler",
                instrument=trade.instrument,
                payload={"trade_id": trade.id, "reason": "no_verifiable_exit_price"},
            )
            continue

        record_trade_close(session, trade.id, exit_price)
        report.closed_while_offline.append(trade.id)
        log_decision(
            session,
            event_type="reconcile_closed_while_offline",
            agent_id="reconciler",
            instrument=trade.instrument,
            payload={"trade_id": trade.id, "exit_price": exit_price},
        )

    for position in broker_positions:
        identity = position.broker_position_id or position.client_order_id
        if identity not in matched_broker_ids:
            report.untracked_at_broker.append(f"{position.instrument}:{identity}")
            log_decision(
                session,
                event_type="reconcile_untracked_broker_position",
                agent_id="reconciler",
                instrument=position.instrument,
                payload={"broker_position_id": position.broker_position_id, "action": "reported_only"},
            )

    log_decision(
        session,
        event_type="reconcile_completed",
        agent_id="reconciler",
        payload={
            "closed_while_offline": report.closed_while_offline,
            "still_open": len(report.still_open),
            "untracked_at_broker": report.untracked_at_broker,
            "unresolved": report.unresolved,
        },
    )
    return report


def _resolve_offline_exit_price(execution: ExecutionAdapter, broker_position_id: str | None) -> float | None:
    """The broker's actual fill price for a position that closed while we were
    down, from its deal history.

    Returns None when it can't be established — the caller then records
    nothing rather than inventing a number, because a fabricated exit price
    would silently corrupt the very expectancy stats the learning loop uses to
    decide which strategies to keep running.
    """
    if broker_position_id is None:
        return None
    resolver = getattr(execution, "get_closed_position_exit_price", None)
    if resolver is None:
        return None
    try:
        return resolver(broker_position_id)
    except Exception:  # noqa: BLE001 - unavailable history is not an error, just unknown
        return None
