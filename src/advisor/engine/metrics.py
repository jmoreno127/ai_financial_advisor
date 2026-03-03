from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from statistics import mean, pstdev
from typing import Deque, Dict, Iterable, List

from advisor.models import InstrumentSnapshot, PortfolioSnapshot, RiskMetrics


@dataclass
class RollingWindowState:
    maxlen: int = 240
    portfolio_pnl_pct_history: Deque[float] = field(default_factory=lambda: deque(maxlen=240))
    instrument_pct_history: Dict[str, Deque[float]] = field(
        default_factory=lambda: defaultdict(lambda: deque(maxlen=240))
    )

    def update(self, portfolio: PortfolioSnapshot, instruments: Iterable[InstrumentSnapshot]) -> None:
        pnl_pct = _safe_div(portfolio.daily_pnl, portfolio.net_liquidation) * 100.0
        self.portfolio_pnl_pct_history.append(pnl_pct)
        for instrument in instruments:
            self.instrument_pct_history[instrument.symbol].append(instrument.pct_change)

    def portfolio_pnl_delta_pct(self) -> float:
        if len(self.portfolio_pnl_pct_history) < 2:
            return 0.0
        return self.portfolio_pnl_pct_history[-1] - self.portfolio_pnl_pct_history[-2]

    def instrument_zscore(self, symbol: str, value: float) -> float:
        history = self.instrument_pct_history.get(symbol)
        if history is None or len(history) < 10:
            return 0.0
        mu = mean(history)
        sigma = pstdev(history)
        if sigma == 0:
            return 0.0
        return (value - mu) / sigma


def compute_risk_metrics(
    portfolio: PortfolioSnapshot,
    max_margin_utilization: float,
    max_single_name_exposure: float,
    max_gross_leverage: float,
    max_drawdown_from_day_high: float,
) -> RiskMetrics:
    gross_leverage = _safe_div(portfolio.gross_position_value, portfolio.net_liquidation)
    margin_utilization = _safe_div(portfolio.init_margin_req, portfolio.net_liquidation)
    cushion = _safe_div(portfolio.excess_liquidity, portfolio.net_liquidation)

    largest_mv = max((abs(position.market_value) for position in portfolio.positions), default=0.0)
    largest_position_weight = _safe_div(largest_mv, portfolio.net_liquidation)

    drawdown_from_day_high = 0.0
    if portfolio.day_high_equity > 0:
        drawdown_from_day_high = max(
            0.0,
            (portfolio.day_high_equity - portfolio.net_liquidation) / portfolio.day_high_equity,
        )

    margin_ok = margin_utilization <= max_margin_utilization
    leverage_ok = gross_leverage <= max_gross_leverage
    concentration_ok = largest_position_weight <= max_single_name_exposure
    drawdown_ok = drawdown_from_day_high <= max_drawdown_from_day_high

    breaches: List[str] = []
    if not margin_ok:
        breaches.append("margin")
    if not leverage_ok:
        breaches.append("leverage")
    if not concentration_ok:
        breaches.append("concentration")
    if not drawdown_ok:
        breaches.append("drawdown")

    near_breach = (
        margin_utilization >= max_margin_utilization * 0.95
        or gross_leverage >= max_gross_leverage * 0.95
        or largest_position_weight >= max_single_name_exposure * 0.95
        or drawdown_from_day_high >= max_drawdown_from_day_high * 0.95
    )

    return RiskMetrics(
        gross_leverage=gross_leverage,
        margin_utilization=margin_utilization,
        cushion=cushion,
        largest_position_weight=largest_position_weight,
        drawdown_from_day_high=drawdown_from_day_high,
        margin_ok=margin_ok,
        leverage_ok=leverage_ok,
        concentration_ok=concentration_ok,
        drawdown_ok=drawdown_ok,
        near_breach=near_breach,
        breaches=breaches,
    )


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator
