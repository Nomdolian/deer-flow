from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.db.models import Direction


@dataclass(frozen=True, slots=True)
class OrderRequest:
    client_order_id: str  # caller-generated UUID; adapters must be idempotent on this
    instrument: str
    direction: Direction
    size: float
    stop_loss: float
    take_profit: float
    price: float | None = None  # None == market order


@dataclass(frozen=True, slots=True)
class OrderResult:
    client_order_id: str
    broker_order_id: str | None
    status: str  # "filled" | "rejected" | "pending" | "error"
    filled_price: float | None = None
    error: str | None = None
    # The broker's position identifier (MT5 position ticket). Durable identity
    # for later modify/close — see OrderRecord.broker_position_id for why the
    # comment field can't be trusted for this.
    broker_position_id: str | None = None


@dataclass(frozen=True, slots=True)
class OpenPositionSnapshot:
    instrument: str
    direction: Direction
    size: float
    entry_price: float
    stop_loss: float
    take_profit: float
    unrealized_pnl: float
    client_order_id: str
    broker_position_id: str | None = None


class ExecutionAdapter(ABC):
    """Common interface every broker/exchange adapter implements. This is the
    ONLY layer allowed to talk to broker/exchange APIs — no other service,
    especially not the LLM layer, gets execution credentials (Phase 4, item 4)."""

    name: str

    @abstractmethod
    def place_order(self, request: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def modify_order(self, client_order_id: str, *, stop_loss: float | None = None, take_profit: float | None = None) -> OrderResult: ...

    @abstractmethod
    def close_position(self, client_order_id: str) -> OrderResult: ...

    @abstractmethod
    def get_open_positions(self) -> list[OpenPositionSnapshot]: ...

    @abstractmethod
    def get_equity(self) -> float: ...

    def is_connected(self) -> bool:
        """Whether the adapter currently has a working broker connection.
        Adapters with no connection to lose (the paper simulator) are always
        connected. Used by the connectivity watchdog, which fails closed:
        a prolonged disconnect engages the kill switch."""
        return True

    def is_tradeable(self, instrument: str) -> bool:
        """Whether the venue will accept an order on this instrument right now.
        This is how the system distinguishes "market closed" (expected quiet —
        weekends on forex, a broker's crypto maintenance window) from "feed
        broken" (a fault worth alerting on). Adapters that can't tell report
        True and let the normal order path reject if wrong."""
        return True
