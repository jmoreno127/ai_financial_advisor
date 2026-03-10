from __future__ import annotations

from datetime import datetime, timezone

from advisor.trading.strategies.conflicts import choose_best_signal
from advisor.trading.types import Side, TradeSetup


def test_conflict_chooses_highest_expected_r_minus_penalty() -> None:
    ts = datetime(2026, 1, 5, tzinfo=timezone.utc)
    a = TradeSetup(
        symbol="MES-202606-CME",
        timestamp=ts,
        side=Side.LONG,
        entry_price=100,
        stop_price=99,
        target_price=102,
        strategy_name="orb",
        reason_codes=["a"],
        expected_r=1.8,
        risk_penalty=0.6,
    )
    b = TradeSetup(
        symbol="MES-202606-CME",
        timestamp=ts,
        side=Side.SHORT,
        entry_price=100,
        stop_price=101,
        target_price=98,
        strategy_name="vwap_pullback",
        reason_codes=["b"],
        expected_r=1.4,
        risk_penalty=0.1,
    )

    selected = choose_best_signal([a, b])
    assert selected is not None
    assert selected.strategy_name == "vwap_pullback"
