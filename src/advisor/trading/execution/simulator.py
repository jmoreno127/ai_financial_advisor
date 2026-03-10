from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from advisor.trading.config import TradingConfig
from advisor.trading.types import ExitReason, FillEvent, PositionState, Side


@dataclass(slots=True)
class ExecutionSimulator:
    config: TradingConfig

    def apply_slippage(self, price: float, side: Side, entry: bool, symbol: str) -> float:
        spec = self._instrument_spec(symbol)
        tick = float(spec.get("tick_size", 0.25))
        slip_ticks = max(0, int(self.config.execution.slippage_ticks))
        adjustment = slip_ticks * tick

        if entry:
            if side == Side.LONG:
                return price + adjustment
            return price - adjustment

        if side == Side.LONG:
            return price - adjustment
        return price + adjustment

    def process_bar(
        self,
        position: PositionState,
        *,
        bar_ts: datetime,
        high: float,
        low: float,
        close: float,
        force_flat: bool = False,
        exit_on_signal: bool = False,
    ) -> Optional[FillEvent]:
        stop_hit = False
        target_hit = False

        if position.side == Side.LONG:
            stop_hit = low <= position.stop_price
            target_hit = position.target_price is not None and high >= position.target_price
            mfe = high - position.entry_price
            mae = min(0.0, low - position.entry_price)
        else:
            stop_hit = high >= position.stop_price
            target_hit = position.target_price is not None and low <= position.target_price
            mfe = position.entry_price - low
            mae = min(0.0, position.entry_price - high)

        position.mfe = max(position.mfe, mfe)
        position.mae = min(position.mae, mae)

        if stop_hit and target_hit:
            if self.config.execution.same_bar_resolution.lower() == "pessimistic":
                return self._close(position, bar_ts, position.stop_price, ExitReason.STOP)
            if self.config.execution.same_bar_resolution.lower() == "optimistic":
                target = position.target_price if position.target_price is not None else close
                return self._close(position, bar_ts, target, ExitReason.TARGET)

            # nearest-first fallback
            target = position.target_price if position.target_price is not None else close
            if abs(position.entry_price - position.stop_price) <= abs(target - position.entry_price):
                return self._close(position, bar_ts, position.stop_price, ExitReason.STOP)
            return self._close(position, bar_ts, target, ExitReason.TARGET)

        if stop_hit:
            return self._close(position, bar_ts, position.stop_price, ExitReason.STOP)
        if target_hit and position.target_price is not None:
            return self._close(position, bar_ts, position.target_price, ExitReason.TARGET)
        if exit_on_signal:
            return self._close(position, bar_ts, close, ExitReason.TIME)
        if force_flat:
            return self._close(position, bar_ts, close, ExitReason.END_OF_DAY)
        return None

    def _close(self, position: PositionState, ts: datetime, price: float, reason: ExitReason) -> FillEvent:
        exit_price = self.apply_slippage(price, position.side, entry=False, symbol=position.symbol)
        point_value = float(self._instrument_spec(position.symbol).get("point_value", 1.0))

        signed_points = (exit_price - position.entry_price) if position.side == Side.LONG else (position.entry_price - exit_price)
        gross = signed_points * point_value * position.contracts
        commission = float(self.config.execution.commission_per_side) * 2 * position.contracts
        net = gross - commission

        risk_points = abs(position.entry_price - position.stop_price)
        initial_risk = risk_points * point_value * position.contracts

        return FillEvent(
            symbol=position.symbol,
            strategy_name=position.strategy_name,
            side=position.side,
            contracts=position.contracts,
            entry_ts=position.opened_at,
            exit_ts=ts,
            entry_price=position.entry_price,
            exit_price=exit_price,
            stop_price=position.stop_price,
            target_price=position.target_price,
            initial_risk_dollars=initial_risk,
            realized_pnl_gross=gross,
            commissions=commission,
            realized_pnl_net=net,
            mae=position.mae,
            mfe=position.mfe,
            holding_minutes=max((ts - position.opened_at).total_seconds() / 60.0, 0.0),
            exit_reason=reason,
            reason_codes=list(position.reason_codes),
        )

    def _instrument_spec(self, symbol: str) -> dict:
        root = symbol.split("-")[0].split(":")[0].upper()
        return self.config.instrument_specs.get(root, {"point_value": 1.0, "tick_size": 0.25})
