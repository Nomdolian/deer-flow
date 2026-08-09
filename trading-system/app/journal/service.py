from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.models import OrderRecord, SignalRecord, TradeJournalRecord
from app.logging_utils import log_decision
from app.notifications.service import notify_position_closed
from app.risk.manager import record_trade_result


def record_trade_open(session: Session, order: OrderRecord, signal: SignalRecord, size: float) -> TradeJournalRecord:
    """Every closed trade must be reconstructable from data written BEFORE it
    happened (Phase 0, item 4 / non-negotiable constraints) — so the journal row
    is created at open time, not synthesized after close."""
    trade = TradeJournalRecord(
        order_id=order.id,
        strategy_id=signal.strategy_id,
        strategy_version=signal.strategy_version,
        instrument=signal.instrument,
        asset_class=signal.asset_class,
        direction=signal.direction,
        confluences=signal.confluences,
        size=size,
        entry_price=order.filled_price or signal.entry,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        opened_at=datetime.now(UTC),
    )
    session.add(trade)
    session.commit()
    session.refresh(trade)
    log_decision(session, event_type="trade_opened", agent_id="journal", instrument=trade.instrument, payload={"trade_id": trade.id})
    return trade


def find_open_trade(
    session: Session, *, client_order_id: str | None = None, broker_position_id: str | None = None
) -> TradeJournalRecord | None:
    """Look up the still-open journal row for a position, by client order id or
    broker position id.

    This is deliberately a DB query rather than an in-process cache: the
    mapping has to survive the process dying. On a laptop running 24/7 —
    Windows Update reboots, lid closes, crashes — an in-memory dict means any
    position open at that moment can never be matched back to its journal row
    again, so its close would go unrecorded and the strategy's performance
    stats would be silently wrong.
    """
    if client_order_id is None and broker_position_id is None:
        raise ValueError("provide client_order_id or broker_position_id")

    query = session.query(TradeJournalRecord).join(
        OrderRecord, TradeJournalRecord.order_id == OrderRecord.id
    ).filter(TradeJournalRecord.closed_at.is_(None))

    if client_order_id is not None:
        query = query.filter(OrderRecord.client_order_id == client_order_id)
    if broker_position_id is not None:
        query = query.filter(OrderRecord.broker_position_id == broker_position_id)

    return query.order_by(TradeJournalRecord.opened_at.desc()).first()


def list_open_trades(session: Session) -> list[TradeJournalRecord]:
    return (
        session.query(TradeJournalRecord)
        .filter(TradeJournalRecord.closed_at.is_(None))
        .order_by(TradeJournalRecord.opened_at)
        .all()
    )


def record_trade_close(session: Session, trade_id: str, exit_price: float) -> TradeJournalRecord:
    trade = session.get(TradeJournalRecord, trade_id)
    if trade is None:
        raise ValueError(f"unknown trade {trade_id}")

    direction_mult = 1 if trade.direction.value == "long" else -1
    pnl = (exit_price - trade.entry_price) * direction_mult * trade.size
    risk_amount = abs(trade.entry_price - trade.stop_loss) * trade.size
    r_multiple = pnl / risk_amount if risk_amount else 0.0

    trade.exit_price = exit_price
    trade.pnl = pnl
    trade.r_multiple = r_multiple
    trade.outcome = "win" if pnl > 0 else ("loss" if pnl < 0 else "breakeven")
    trade.closed_at = datetime.now(UTC)
    session.commit()

    record_trade_result(session, trade.strategy_id, trade.strategy_version, is_loss=pnl < 0)
    log_decision(
        session,
        event_type="trade_closed",
        agent_id="journal",
        instrument=trade.instrument,
        payload={"trade_id": trade.id, "pnl": pnl, "r_multiple": r_multiple, "outcome": trade.outcome},
    )
    notify_position_closed(session, trade)
    return trade
