from __future__ import annotations

from datetime import datetime, timezone

from advisor.trading.config import TradingConfig
from advisor.trading.execution.simulator import ExecutionSimulator
from advisor.trading.types import ExitReason, PositionState, Side


def test_same_bar_pessimistic_prefers_stop() -> None:
    cfg = TradingConfig()
    cfg.execution.same_bar_resolution = "pessimistic"
    sim = ExecutionSimulator(cfg)

    pos = PositionState(
        symbol="MES-202606-CME",
        side=Side.LONG,
        contracts=1,
        entry_price=100.0,
        stop_price=99.0,
        target_price=101.0,
        opened_at=datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc),
        strategy_name="orb",
    )

    fill = sim.process_bar(
        pos,
        bar_ts=datetime(2026, 1, 5, 15, 5, tzinfo=timezone.utc),
        high=101.5,
        low=98.5,
        close=100.2,
    )
    assert fill is not None
    assert fill.exit_reason == ExitReason.STOP
