from __future__ import annotations

from typing import Dict, List

import numpy as np

from advisor.trading.types import FillEvent


def compute_metrics(trades: List[FillEvent], equity_curve: List[float]) -> Dict[str, float]:
    if not trades:
        return {
            "trades": 0.0,
            "net_pnl": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "average_r": 0.0,
            "stability": 0.0,
        }

    pnls = np.array([t.realized_pnl_net for t in trades], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    gross_win = float(wins.sum()) if wins.size else 0.0
    gross_loss = abs(float(losses.sum())) if losses.size else 0.0
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)

    expectancy = float(np.mean(pnls)) if pnls.size else 0.0
    win_rate = float((pnls > 0).mean()) if pnls.size else 0.0

    risk_values = np.array([max(t.initial_risk_dollars, 1e-9) for t in trades], dtype=float)
    average_r = float(np.mean(pnls / risk_values)) if pnls.size else 0.0

    eq = np.array(equity_curve if equity_curve else [0.0], dtype=float)
    running_max = np.maximum.accumulate(eq)
    drawdowns = np.where(running_max > 0, (running_max - eq) / running_max, 0.0)
    max_drawdown = float(drawdowns.max()) if drawdowns.size else 0.0

    # Stability proxy: lower variance in rolling returns means more stable.
    rets = np.diff(eq)
    stability = 1.0 / (1.0 + float(np.std(rets)) if rets.size else 1.0)

    return {
        "trades": float(len(trades)),
        "net_pnl": float(pnls.sum()),
        "profit_factor": float(profit_factor),
        "expectancy": expectancy,
        "max_drawdown": max_drawdown,
        "win_rate": win_rate,
        "average_r": average_r,
        "stability": float(stability),
    }


def rank_score(metrics: Dict[str, float]) -> float:
    pf = metrics.get("profit_factor", 0.0)
    exp = metrics.get("expectancy", 0.0)
    stability = metrics.get("stability", 0.0)
    dd = metrics.get("max_drawdown", 1.0)
    return (pf * 2.0) + (exp * 0.01) + stability - (dd * 3.0)
