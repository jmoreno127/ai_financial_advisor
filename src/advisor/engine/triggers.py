from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from advisor.config import AppConfig
from advisor.engine.metrics import RollingWindowState
from advisor.models import InstrumentSnapshot, PortfolioSnapshot, RiskMetrics, TriggerEvent


def evaluate_triggers(
    config: AppConfig,
    portfolio: PortfolioSnapshot,
    instruments: List[InstrumentSnapshot],
    rolling: RollingWindowState,
) -> List[TriggerEvent]:
    events: List[TriggerEvent] = []
    now = datetime.now(timezone.utc)

    for instrument in instruments:
        abs_move = abs(instrument.pct_change)
        if abs_move >= config.trigger_move_pct:
            events.append(
                TriggerEvent(
                    name="absolute_move",
                    reason=f"{instrument.symbol} moved {instrument.pct_change:.2f}%",
                    severity="medium",
                    metric_value=abs_move,
                    threshold=config.trigger_move_pct,
                    symbol=instrument.symbol,
                    timestamp=now,
                )
            )

    pnl_delta = abs(rolling.portfolio_pnl_delta_pct())
    if pnl_delta >= config.trigger_pnl_delta_pct:
        events.append(
            TriggerEvent(
                name="portfolio_pnl_delta",
                reason=f"Portfolio PnL delta {pnl_delta:.2f}%",
                severity="high",
                metric_value=pnl_delta,
                threshold=config.trigger_pnl_delta_pct,
                timestamp=now,
            )
        )

    for instrument in instruments:
        zscore = abs(rolling.instrument_zscore(instrument.symbol, instrument.pct_change))
        if zscore >= config.trigger_zscore:
            events.append(
                TriggerEvent(
                    name="zscore_anomaly",
                    reason=f"{instrument.symbol} z-score {zscore:.2f}",
                    severity="high",
                    metric_value=zscore,
                    threshold=config.trigger_zscore,
                    symbol=instrument.symbol,
                    timestamp=now,
                )
            )

    return events


def should_run_deep_analysis(triggers: List[TriggerEvent], risk: RiskMetrics) -> bool:
    return bool(triggers) or risk.near_breach or bool(risk.breaches)
