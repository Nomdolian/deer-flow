import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.data.feed_health import is_stale, record_candle_seen
from app.data.provider_base import DataProvider
from app.db.models import OrderRecord, SignalRecord, StrategyVersion, TradeJournalRecord
from app.execution.base import ExecutionAdapter, OrderRequest
from app.execution.paper_adapter import PaperAdapter
from app.journal.service import record_trade_close, record_trade_open
from app.killswitch.service import KillSwitchEngaged, assert_not_engaged
from app.logging_utils import log_decision
from app.risk.correlation import correlation_group_for
from app.risk.manager import RiskManager
from app.risk.models import OpenPosition, PortfolioState
from app.signals.base import SignalEngine

_OPEN_TRADE_BY_CLIENT_ORDER_ID: dict[str, str] = {}  # client_order_id -> trade_journal id, in-process cache


class Orchestrator:
    """Schedules the pipeline for ONE instrument: data -> signal engines -> risk
    manager -> execution -> journal (Phase 2's architecture diagram, single
    asset class). No LLM call sits anywhere in this path (non-negotiable
    constraint) — engines and the risk manager are pure/DB-backed code.

    Intended usage: call `run_once()` on every new closed candle. Scheduling
    (how often that happens) lives in the calling script, not here — this keeps
    the orchestrator itself synchronous and trivially testable.
    """

    def __init__(
        self,
        session_factory,
        data_provider: DataProvider,
        engines: list[SignalEngine],
        execution: ExecutionAdapter,
        instrument: str,
        timeframe: str,
        risk_manager: RiskManager | None = None,
        lookback: int = 300,
    ):
        self.session_factory = session_factory
        self.data_provider = data_provider
        self.engines = engines
        self.execution = execution
        self.instrument = instrument
        self.timeframe = timeframe
        self.risk_manager = risk_manager or RiskManager()
        self.lookback = lookback

    def run_once(self) -> None:
        session: Session = self.session_factory()
        try:
            self._run_once(session)
        finally:
            session.close()

    def _run_once(self, session: Session) -> None:
        try:
            assert_not_engaged(session)
        except KillSwitchEngaged as exc:
            log_decision(session, event_type="cycle_skipped", agent_id="orchestrator", instrument=self.instrument, payload={"reason": str(exc)})
            return

        try:
            candles = self.data_provider.historical(self.instrument, self.timeframe, self.lookback)
        except Exception as exc:
            # Fail closed: a data error halts new trading for this instrument, it
            # does not proceed with stale/guessed data (non-negotiable constraint).
            log_decision(session, event_type="data_fetch_error", agent_id="orchestrator", instrument=self.instrument, payload={"error": str(exc)})
            return

        if not candles:
            return
        record_candle_seen(session, candles[-1])

        if isinstance(self.execution, PaperAdapter):
            self.execution.update_price(self.instrument, candles[-1].close)
            fills = self.execution.check_stops(self.instrument, candles[-1].low, candles[-1].high)
            for fill in fills:
                trade_id = _OPEN_TRADE_BY_CLIENT_ORDER_ID.pop(fill.client_order_id, None)
                if trade_id and fill.filled_price is not None:
                    record_trade_close(session, trade_id, fill.filled_price)

        if is_stale(session, self.instrument):
            log_decision(session, event_type="cycle_skipped", agent_id="orchestrator", instrument=self.instrument, payload={"reason": "feed_stale"})
            return

        if self.execution.get_open_positions():
            return  # single concurrent position per instrument in this baseline orchestrator

        for engine in self.engines:
            signal = engine.evaluate(candles)
            if signal is None:
                continue
            self._ensure_strategy_version(session, signal.strategy_id, signal.strategy_version, signal.asset_class)
            self._handle_signal(session, signal, engine)
            break  # first accepted-or-not signal this cycle; multi-engine arbitration is a Phase 6 thesis/contradiction concern

    def _handle_signal(self, session: Session, signal, engine: SignalEngine) -> None:
        portfolio = self._portfolio_state(session)
        result = self.risk_manager.evaluate(session, signal, portfolio)
        if not result.accepted:
            return

        client_order_id = str(uuid.uuid4())
        request = OrderRequest(
            client_order_id=client_order_id,
            instrument=signal.instrument,
            direction=signal.direction,
            size=result.size,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
        )
        order_result = self.execution.place_order(request)

        order_record = OrderRecord(
            client_order_id=client_order_id,
            instrument=signal.instrument,
            direction=signal.direction,
            requested_size=result.size,
            requested_price=signal.entry,
            filled_price=order_result.filled_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            status=order_result.status,
            broker=self.execution.name,
            error=order_result.error,
            filled_at=datetime.now(UTC) if order_result.status == "filled" else None,
        )
        session.add(order_record)
        session.commit()
        session.refresh(order_record)

        log_decision(
            session,
            event_type="order_placed",
            agent_id="execution",
            instrument=signal.instrument,
            payload={"client_order_id": client_order_id, "status": order_result.status, "error": order_result.error},
        )

        if order_result.status == "filled":
            signal_record = (
                session.query(SignalRecord)
                .filter_by(strategy_id=signal.strategy_id, strategy_version=signal.strategy_version, candle_time=signal.candle_time, instrument=signal.instrument)
                .order_by(SignalRecord.created_at.desc())
                .first()
            )
            if signal_record is not None:
                trade = record_trade_open(session, order_record, signal_record, result.size)
                _OPEN_TRADE_BY_CLIENT_ORDER_ID[client_order_id] = trade.id

    def _portfolio_state(self, session: Session) -> PortfolioState:
        equity = self.execution.get_equity()
        open_positions = [
            OpenPosition(
                instrument=p.instrument,
                asset_class=self.engines[0].asset_class,
                strategy_id="",
                risk_amount=abs(p.entry_price - p.stop_loss) * p.size,
                correlation_group=correlation_group_for(p.instrument),
            )
            for p in self.execution.get_open_positions()
        ]
        now = datetime.now(UTC)
        day_start = now - timedelta(hours=24)
        week_start = now - timedelta(days=7)
        return PortfolioState(
            equity=equity,
            open_positions=open_positions,
            daily_realized_pnl=self._realized_pnl_since(session, day_start),
            daily_starting_equity=equity,
            weekly_realized_pnl=self._realized_pnl_since(session, week_start),
            weekly_starting_equity=equity,
        )

    def _realized_pnl_since(self, session: Session, since: datetime) -> float:
        trades = (
            session.query(TradeJournalRecord)
            .filter(TradeJournalRecord.closed_at.isnot(None), TradeJournalRecord.closed_at >= since)
            .all()
        )
        return sum(t.pnl or 0.0 for t in trades)

    def _ensure_strategy_version(self, session: Session, strategy_id: str, version: int, asset_class) -> None:
        existing = session.query(StrategyVersion).filter_by(strategy_id=strategy_id, version=version).one_or_none()
        if existing is None:
            session.add(StrategyVersion(strategy_id=strategy_id, version=version, asset_class=asset_class))
            session.commit()
