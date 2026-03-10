from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict

from advisor.trading.config import TradingConfig
from advisor.trading.types import RiskDecision, SessionState, TradeSetup


@dataclass(slots=True)
class RiskEngine:
    config: TradingConfig
    equity: float

    def build_session_state(self, ts: datetime) -> SessionState:
        return SessionState(date_key=ts.strftime("%Y-%m-%d"), week_key=f"{ts.year}-{ts.isocalendar().week:02d}")

    def reset_if_needed(self, session_state: SessionState, ts: datetime) -> None:
        date_key = ts.strftime("%Y-%m-%d")
        week_key = f"{ts.year}-{ts.isocalendar().week:02d}"
        if date_key != session_state.date_key:
            session_state.date_key = date_key
            session_state.trades_today.clear()
            session_state.daily_realized_pnl = 0.0
            session_state.lockout_reasons = []
        if week_key != session_state.week_key:
            session_state.week_key = week_key
            session_state.weekly_realized_pnl = 0.0

    def can_trade(self, session_state: SessionState, strategy_name: str) -> RiskDecision:
        profile = self.config.active_risk_profile
        if session_state.lockout_reasons:
            return RiskDecision(False, session_state.lockout_reasons[-1], 0, 0.0)

        max_trades = int(profile["max_trades_per_day_per_strategy"])
        if session_state.trades_today.get(strategy_name, 0) >= max_trades:
            return RiskDecision(False, "max_trades_reached", 0, 0.0)

        if session_state.consecutive_losses >= int(profile["max_consecutive_losses"]):
            return RiskDecision(False, "consecutive_losses_limit_reached", 0, 0.0)

        daily_limit = self.equity * float(profile["max_daily_loss_pct"])
        if abs(min(session_state.daily_realized_pnl, 0.0)) >= daily_limit:
            return RiskDecision(False, "daily_loss_limit_reached", 0, 0.0)

        weekly_limit = self.equity * float(profile["max_weekly_loss_pct"])
        if abs(min(session_state.weekly_realized_pnl, 0.0)) >= weekly_limit:
            return RiskDecision(False, "weekly_loss_limit_reached", 0, 0.0)

        return RiskDecision(True, "ok", 0, 0.0)

    def size_trade(self, setup: TradeSetup, symbol: str) -> RiskDecision:
        profile = self.config.active_risk_profile
        risk_budget = self.equity * float(profile["risk_per_trade_pct"])

        spec = self._instrument_spec(symbol)
        point_value = float(spec.get("point_value", 1.0))
        stop_distance = abs(setup.entry_price - setup.stop_price)
        if stop_distance <= 0:
            return RiskDecision(False, "invalid_stop_distance", 0, 0.0)

        dollar_risk_per_contract = stop_distance * point_value
        if dollar_risk_per_contract <= 0:
            return RiskDecision(False, "invalid_risk", 0, 0.0)

        contracts = int(risk_budget // dollar_risk_per_contract)
        if contracts <= 0:
            return RiskDecision(False, "risk_budget_exceeded", 0, 0.0)

        return RiskDecision(True, "ok", contracts, contracts * dollar_risk_per_contract)

    def register_entry(self, session_state: SessionState, strategy_name: str) -> None:
        session_state.trades_today[strategy_name] = session_state.trades_today.get(strategy_name, 0) + 1

    def register_trade_result(self, session_state: SessionState, pnl_net: float) -> None:
        session_state.daily_realized_pnl += pnl_net
        session_state.weekly_realized_pnl += pnl_net
        self.equity += pnl_net

        if pnl_net < 0:
            session_state.consecutive_losses += 1
        else:
            session_state.consecutive_losses = 0

        profile = self.config.active_risk_profile
        daily_limit = self.equity * float(profile["max_daily_loss_pct"])
        weekly_limit = self.equity * float(profile["max_weekly_loss_pct"])

        if abs(min(session_state.daily_realized_pnl, 0.0)) >= daily_limit:
            session_state.lockout_reasons.append("daily_loss_limit_reached")
        if abs(min(session_state.weekly_realized_pnl, 0.0)) >= weekly_limit:
            session_state.lockout_reasons.append("weekly_loss_limit_reached")
        if session_state.consecutive_losses >= int(profile["max_consecutive_losses"]):
            session_state.lockout_reasons.append("consecutive_losses_limit_reached")

    def _instrument_spec(self, symbol: str) -> Dict[str, float]:
        root = symbol.split("-")[0].split(":")[0].upper()
        spec = self.config.instrument_specs.get(root)
        if not spec:
            return {"point_value": 1.0, "tick_size": 0.25, "tick_value": 0.25}
        return spec
