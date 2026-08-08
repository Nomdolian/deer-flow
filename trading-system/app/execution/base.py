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
