from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from advisor.trading.strategies.base import BaseStrategy, StrategyContext
from advisor.trading.types import PositionState, Side, SignalAction, StrategySignal, TradeSetup


@dataclass(slots=True)
class VWAPParams:
    pullback_band_atr_mult: float
    target_r_multiple: float


class VWAPPullbackStrategy(BaseStrategy):
    name = "vwap_pullback"

    def __init__(self, params: VWAPParams):
        self.params = params

    def prepare_features(self, data: pd.DataFrame) -> pd.DataFrame:
        out = data.copy()
        out["ema_fast"] = out.groupby("symbol")["close"].transform(lambda s: s.ewm(span=9, adjust=False).mean())
        out["ema_slow"] = out.groupby("symbol")["close"].transform(lambda s: s.ewm(span=21, adjust=False).mean())
        return out

    def validate_entry(self, context: StrategyContext) -> bool:
        row = context.row
        atr = float(row.get("atr", 0.0) or 0.0)
        if atr <= 0:
            return False
        volume = float(row.get("volume", 0.0) or 0.0)
        avg_volume = float(row.get("volume_avg", 0.0) or 0.0)
        return volume >= 0.5 * max(avg_volume, 1.0)

    def generate_entry(self, context: StrategyContext) -> StrategySignal:
        if not self.validate_entry(context):
            return StrategySignal(action=SignalAction.NO_ACTION)

        row = context.row
        close_price = float(row["close"])
        vwap = float(row.get("vwap", close_price) or close_price)
        atr = max(float(row.get("atr", 0.0) or 0.0), 0.01)
        band = atr * self.params.pullback_band_atr_mult
        trend_up = float(row.get("ema_fast", close_price)) > float(row.get("ema_slow", close_price))
        trend_down = float(row.get("ema_fast", close_price)) < float(row.get("ema_slow", close_price))

        if trend_up and close_price >= vwap and abs(close_price - vwap) <= band:
            risk = max(atr * 0.8, 0.01)
            setup = TradeSetup(
                symbol=str(row["symbol"]),
                timestamp=row["timestamp"],
                side=Side.LONG,
                entry_price=close_price,
                stop_price=close_price - risk,
                target_price=close_price + risk * self.params.target_r_multiple,
                strategy_name=self.name,
                reason_codes=["vwap_pullback_long"],
                expected_r=self.params.target_r_multiple,
                risk_penalty=risk / atr,
            )
            return StrategySignal(action=SignalAction.ENTRY, setup=setup)

        if trend_down and close_price <= vwap and abs(close_price - vwap) <= band:
            risk = max(atr * 0.8, 0.01)
            setup = TradeSetup(
                symbol=str(row["symbol"]),
                timestamp=row["timestamp"],
                side=Side.SHORT,
                entry_price=close_price,
                stop_price=close_price + risk,
                target_price=close_price - risk * self.params.target_r_multiple,
                strategy_name=self.name,
                reason_codes=["vwap_pullback_short"],
                expected_r=self.params.target_r_multiple,
                risk_penalty=risk / atr,
            )
            return StrategySignal(action=SignalAction.ENTRY, setup=setup)

        return StrategySignal(action=SignalAction.NO_ACTION)

    def generate_exit(self, context: StrategyContext, position: PositionState) -> StrategySignal:
        row = context.row
        vwap = float(row.get("vwap", row["close"]))
        price = float(row["close"])

        if position.side == Side.LONG and price < vwap:
            return StrategySignal(action=SignalAction.EXIT, reason="vwap_reject")
        if position.side == Side.SHORT and price > vwap:
            return StrategySignal(action=SignalAction.EXIT, reason="vwap_reject")
        return StrategySignal(action=SignalAction.NO_ACTION)
