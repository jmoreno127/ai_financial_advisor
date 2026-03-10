from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class SignalAction(str, Enum):
    NO_ACTION = "NO_ACTION"
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    CANCEL = "CANCEL"


class ExitReason(str, Enum):
    STOP = "STOP"
    TARGET = "TARGET"
    TIME = "TIME"
    END_OF_DAY = "END_OF_DAY"
    MANUAL = "MANUAL"


@dataclass(slots=True)
class TradeSetup:
    symbol: str
    timestamp: datetime
    side: Side
    entry_price: float
    stop_price: float
    target_price: float | None
    strategy_name: str
    reason_codes: List[str]
    confidence: float = 0.0
    expected_r: float = 0.0
    risk_penalty: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RiskDecision:
    approved: bool
    reason: str
    contracts: int
    dollar_risk: float


@dataclass(slots=True)
class OrderIntent:
    symbol: str
    timestamp: datetime
    side: Side
    contracts: int
    entry_price: float
    stop_price: float
    target_price: float | None
    strategy_name: str
    order_type: str = "STOP_MARKET"


@dataclass(slots=True)
class FillEvent:
    symbol: str
    strategy_name: str
    side: Side
    contracts: int
    entry_ts: datetime
    exit_ts: datetime
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float | None
    initial_risk_dollars: float
    realized_pnl_gross: float
    commissions: float
    realized_pnl_net: float
    mae: float
    mfe: float
    holding_minutes: float
    exit_reason: ExitReason
    reason_codes: List[str] = field(default_factory=list)


@dataclass(slots=True)
class PositionState:
    symbol: str
    side: Side
    contracts: int
    entry_price: float
    stop_price: float
    target_price: float | None
    opened_at: datetime
    strategy_name: str
    reason_codes: List[str] = field(default_factory=list)
    mae: float = 0.0
    mfe: float = 0.0


@dataclass(slots=True)
class SessionState:
    date_key: str
    week_key: str
    trades_today: Dict[str, int] = field(default_factory=dict)
    consecutive_losses: int = 0
    daily_realized_pnl: float = 0.0
    weekly_realized_pnl: float = 0.0
    lockout_reasons: List[str] = field(default_factory=list)


@dataclass(slots=True)
class ValidationResult:
    passed: bool
    strategy_name: str
    variant_name: str
    score: float
    oos_profit_factor: float
    oos_max_drawdown: float
    oos_trades: int
    oos_expectancy: float
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BacktestReport:
    strategy_name: str
    variant_name: str
    metrics: Dict[str, float]
    trades: List[FillEvent]
    equity_curve: List[float]
    drawdown_curve: List[float]


@dataclass(slots=True)
class StrategySignal:
    action: SignalAction
    setup: Optional[TradeSetup] = None
    reason: str = ""
