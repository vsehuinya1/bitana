"""
Bitana Core Data Models

Pydantic models and enums for the entire system.
Includes position lifecycle state machine (AD-4).
All trade records linked by trade_uuid (AD-8).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class EngineType(str, Enum):
    COMPRESSION = "COMPRESSION"
    SQUEEZE = "SQUEEZE"
    SWING_BREAK = "SWING_BREAK"


class AlertTier(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class BrakeType(str, Enum):
    DAILY_LOSS = "DAILY_LOSS"
    WEEKLY_LOSS = "WEEKLY_LOSS"
    CONSECUTIVE_LOSS = "CONSECUTIVE_LOSS"
    EQUITY_PAUSE = "EQUITY_PAUSE"
    EQUITY_SHUTDOWN = "EQUITY_SHUTDOWN"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class PositionState(str, Enum):
    """Explicit position lifecycle state machine (AD-4).

    Valid transitions:
        SIGNAL_GENERATED -> ORDER_PENDING
        ORDER_PENDING -> PARTIALLY_FILLED | FILLED | CANCELLED
        PARTIALLY_FILLED -> FILLED | CANCELLED
        FILLED -> STOP_PLACED
        STOP_PLACED -> MANAGING
        MANAGING -> PARTIAL_TP | CLOSING
        PARTIAL_TP -> TRAILING | CLOSING
        TRAILING -> CLOSING
        CLOSING -> CLOSED

    External positions (detected by reconciliation):
        DETECTED -> TRACKING -> CLOSED
    """
    SIGNAL_GENERATED = "SIGNAL_GENERATED"
    ORDER_PENDING = "ORDER_PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    STOP_PLACED = "STOP_PLACED"
    MANAGING = "MANAGING"
    PARTIAL_TP = "PARTIAL_TP"
    TRAILING = "TRAILING"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    # External position states
    DETECTED = "DETECTED"
    TRACKING = "TRACKING"
    CANCELLED = "CANCELLED"


# Valid state transitions
VALID_TRANSITIONS: dict[PositionState, set[PositionState]] = {
    PositionState.SIGNAL_GENERATED: {PositionState.ORDER_PENDING},
    PositionState.ORDER_PENDING: {
        PositionState.PARTIALLY_FILLED,
        PositionState.FILLED,
        PositionState.CANCELLED,
    },
    PositionState.PARTIALLY_FILLED: {
        PositionState.FILLED,
        PositionState.CANCELLED,
    },
    PositionState.FILLED: {PositionState.STOP_PLACED},
    PositionState.STOP_PLACED: {PositionState.MANAGING},
    PositionState.MANAGING: {PositionState.PARTIAL_TP, PositionState.CLOSING},
    PositionState.PARTIAL_TP: {PositionState.TRAILING, PositionState.CLOSING},
    PositionState.TRAILING: {PositionState.CLOSING},
    PositionState.CLOSING: {PositionState.CLOSED},
    PositionState.CLOSED: set(),
    PositionState.DETECTED: {PositionState.TRACKING},
    PositionState.TRACKING: {PositionState.CLOSED},
    PositionState.CANCELLED: set(),
}


def validate_transition(current: PositionState, target: PositionState) -> bool:
    """Check if a state transition is valid."""
    return target in VALID_TRANSITIONS.get(current, set())


# ---------------------------------------------------------------------------
# Core Models
# ---------------------------------------------------------------------------

def _trade_uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.utcnow()


class Candle(BaseModel):
    """Single OHLCV candle."""
    symbol: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def total_range(self) -> float:
        return self.high - self.low


class Signal(BaseModel):
    """Trading signal from an engine."""
    trade_uuid: str = Field(default_factory=_trade_uuid)
    engine: EngineType
    symbol: str
    side: Side
    timestamp: datetime = Field(default_factory=_now)
    entry_price: float
    stop_price: float
    signal_data: dict = Field(default_factory=dict)
    confidence: float = 1.0

    @property
    def risk_distance(self) -> float:
        return abs(self.entry_price - self.stop_price)

    @property
    def r_per_unit(self) -> float:
        if self.risk_distance == 0:
            return 0.0
        return self.risk_distance / self.entry_price


class OrderRequest(BaseModel):
    """Order to be placed."""
    trade_uuid: str
    client_order_id: str = ""
    symbol: str
    side: Side
    quantity: float
    order_type: str = "MARKET"
    price: Optional[float] = None
    stop_price: Optional[float] = None
    reduce_only: bool = False


class OrderResult(BaseModel):
    """Result of an order execution."""
    trade_uuid: str
    client_order_id: str
    exchange_order_id: str = ""
    symbol: str
    side: Side
    status: OrderStatus
    requested_qty: float
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    commission: float = 0.0
    commission_asset: str = "USDT"
    timestamp: datetime = Field(default_factory=_now)
    raw: dict = Field(default_factory=dict)


class Position(BaseModel):
    """Active position with lifecycle state."""
    trade_uuid: str
    symbol: str
    side: Side
    engine: EngineType
    state: PositionState = PositionState.SIGNAL_GENERATED

    # Entry
    entry_price: float = 0.0
    entry_time: Optional[datetime] = None
    quantity: float = 0.0
    leverage: int = 1

    # Risk
    stop_price: float = 0.0
    initial_stop: float = 0.0
    risk_r: float = 0.0

    # Profit taking
    tp1_price: float = 0.0
    tp1_hit: bool = False
    trailing_stop: float = 0.0
    trailing_active: bool = False

    # Accounting
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    commission_total: float = 0.0
    funding_fees: float = 0.0
    candles_held: int = 0

    # Metadata
    externally_managed: bool = False
    client_order_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    def transition_to(self, new_state: PositionState) -> bool:
        """Attempt state transition. Returns True if valid, False if rejected."""
        if validate_transition(self.state, new_state):
            self.state = new_state
            self.updated_at = datetime.utcnow()
            return True
        return False


class TradeRecord(BaseModel):
    """Completed trade record for persistence and metrics."""
    trade_uuid: str
    timestamp: datetime = Field(default_factory=_now)
    engine: EngineType
    symbol: str
    side: Side
    entry_price: float
    exit_price: float
    quantity: float
    leverage: int
    initial_stop: float
    commission: float
    slippage_est: float = 0.0
    funding_fees: float = 0.0
    pnl_usd: float
    pnl_r: float
    hold_time_s: float
    hold_candles: int = 0
    exit_reason: str
    signal_data: dict = Field(default_factory=dict)


class RiskState(BaseModel):
    """Persistent risk state."""
    peak_equity: float = 0.0
    current_equity: float = 0.0
    current_drawdown_pct: float = 0.0
    risk_pct_active: float = 1.5
    consecutive_losses: int = 0
    reduced_risk_trades_remaining: int = 0


class BrakeState(BaseModel):
    """Persistent brake state."""
    daily_realized_loss: float = 0.0
    daily_reset_date: str = ""  # YYYY-MM-DD
    weekly_realized_loss: float = 0.0
    weekly_reset_date: str = ""  # YYYY-MM-DD (Monday)
    weekly_cooldown_until: Optional[datetime] = None
    is_paused: bool = False
    pause_reason: str = ""
    is_shutdown: bool = False
    shutdown_reason: str = ""
    manual_review_required: bool = False
