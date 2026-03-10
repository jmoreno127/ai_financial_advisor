from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from advisor.trading.data.loader import opening_range
from advisor.trading.strategies.base import BaseStrategy, StrategyContext
from advisor.trading.types import PositionState, Side, SignalAction, StrategySignal, TradeSetup


@dataclass(slots=True)
class ORBParams:
    opening_range_minutes: int
    min_range_points: float
    max_range_points: float
    target_r_multiple: float
    one_trade_per_day: bool = True


class ORBStrategy(BaseStrategy):
    name = "orb"

    def __init__(self, params: ORBParams):
        self.params = params

    def prepare_features(self, data: pd.DataFrame) -> pd.DataFrame:
        return opening_range(data, self.params.opening_range_minutes)

    def validate_entry(self, context: StrategyContext) -> bool:
        row = context.row
        if not bool(row.get("or_ready", False)):
            return False

        or_high = float(row.get("or_high", 0.0) or 0.0)
        or_low = float(row.get("or_low", 0.0) or 0.0)
        if or_high <= or_low:
            return False

        range_points = or_high - or_low
        if range_points < self.params.min_range_points or range_points > self.params.max_range_points:
            return False

        if self.params.one_trade_per_day:
            key = f"orb_taken::{row['trade_date']}"
            if context.state.get(key):
                return False

        return True

    def generate_entry(self, context: StrategyContext) -> StrategySignal:
        if not self.validate_entry(context):
            return StrategySignal(action=SignalAction.NO_ACTION)

        row = context.row
        close_price = float(row["close"])
        or_high = float(row.get("or_high", 0.0) or 0.0)
        or_low = float(row.get("or_low", 0.0) or 0.0)
        atr = max(float(row.get("atr", 0.0) or 0.0), 0.01)

        if close_price > or_high:
            stop = or_low
            risk = max(close_price - stop, atr * 0.25)
            setup = TradeSetup(
                symbol=str(row["symbol"]),
                timestamp=row["timestamp"],
                side=Side.LONG,
                entry_price=close_price,
                stop_price=close_price - risk,
                target_price=close_price + risk * self.params.target_r_multiple,
                strategy_name=self.name,
                reason_codes=["orb_breakout_up"],
                expected_r=self.params.target_r_multiple,
                risk_penalty=risk / max(atr, 0.01),
            )
            return StrategySignal(action=SignalAction.ENTRY, setup=setup)

        if close_price < or_low:
            stop = or_high
            risk = max(stop - close_price, atr * 0.25)
            setup = TradeSetup(
                symbol=str(row["symbol"]),
                timestamp=row["timestamp"],
                side=Side.SHORT,
                entry_price=close_price,
                stop_price=close_price + risk,
                target_price=close_price - risk * self.params.target_r_multiple,
                strategy_name=self.name,
                reason_codes=["orb_breakout_down"],
                expected_r=self.params.target_r_multiple,
                risk_penalty=risk / max(atr, 0.01),
            )
            return StrategySignal(action=SignalAction.ENTRY, setup=setup)

        return StrategySignal(action=SignalAction.NO_ACTION)

    def generate_exit(self, context: StrategyContext, position: PositionState) -> StrategySignal:
        row = context.row
        # End-of-day flatten handled by execution engine time rule.
        _ = (row, position)
        return StrategySignal(action=SignalAction.NO_ACTION)
