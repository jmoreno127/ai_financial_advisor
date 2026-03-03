from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class DecisionType(str, Enum):
    NO_ACTION = "NO_ACTION"
    SUGGEST_ACTION = "SUGGEST_ACTION"


class ActionType(str, Enum):
    ADD = "ADD"
    REDUCE = "REDUCE"
    HEDGE = "HEDGE"
    EXIT_PARTIAL = "EXIT_PARTIAL"
    HOLD = "HOLD"


class PositionSnapshot(BaseModel):
    symbol: str
    con_id: Optional[int] = None
    quantity: float
    market_price: float = 0.0
    market_value: float = 0.0
    average_cost: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0


class InstrumentSnapshot(BaseModel):
    symbol: str
    con_id: Optional[int] = None
    last_price: float = 0.0
    previous_close: float = 0.0
    pct_change: float = 0.0
    volume: float = 0.0
    source: str = "watchlist"
    timestamp: datetime


class PortfolioSnapshot(BaseModel):
    cycle_ts: datetime
    account_id: str
    net_liquidation: float
    init_margin_req: float
    excess_liquidity: float
    gross_position_value: float
    daily_pnl: float
    total_unrealized_pnl: float
    day_high_equity: float
    positions: List[PositionSnapshot] = Field(default_factory=list)


class RiskMetrics(BaseModel):
    gross_leverage: float
    margin_utilization: float
    cushion: float
    largest_position_weight: float
    drawdown_from_day_high: float
    margin_ok: bool
    leverage_ok: bool
    concentration_ok: bool
    drawdown_ok: bool
    near_breach: bool = False
    breaches: List[str] = Field(default_factory=list)


class TriggerEvent(BaseModel):
    name: str
    reason: str
    severity: str
    metric_value: float
    threshold: float
    symbol: Optional[str] = None
    timestamp: datetime


class AnalysisRequest(BaseModel):
    cycle_ts: datetime
    portfolio: PortfolioSnapshot
    risk_metrics: RiskMetrics
    triggers: List[TriggerEvent]
    key_instruments: List[InstrumentSnapshot]


class RiskChecks(BaseModel):
    margin_ok: bool
    leverage_ok: bool
    concentration_ok: bool


class Recommendation(BaseModel):
    decision: DecisionType
    action_type: ActionType
    target_symbols: List[str] = Field(default_factory=list)
    rationale: str
    risk_checks: RiskChecks
    confidence: float = 0.0
    ttl_minutes: int = 15
    monitoring_note: str = ""

    @field_validator("confidence")
    @classmethod
    def _confidence_bounds(cls, value: float) -> float:
        if value < 0.0:
            return 0.0
        if value > 1.0:
            return 1.0
        return value


class DecisionRecord(BaseModel):
    cycle_ts: datetime
    account_id: str
    model_used: str
    deep_analysis: bool
    request_payload: Dict[str, Any]
    recommendation: Recommendation
    raw_response: Optional[str] = None
