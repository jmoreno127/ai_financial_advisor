from __future__ import annotations

from datetime import datetime, timezone

from advisor.trading.config import TradingConfig
from advisor.trading.risk.engine import RiskEngine
from advisor.trading.types import Side, TradeSetup


def test_risk_engine_sizes_trade_and_triggers_lockout() -> None:
    cfg = TradingConfig()
    risk = RiskEngine(cfg, equity=57000.0)
    session = risk.build_session_state(datetime(2026, 1, 5, tzinfo=timezone.utc))

    setup = TradeSetup(
        symbol="MES-202606-CME",
        timestamp=datetime(2026, 1, 5, tzinfo=timezone.utc),
        side=Side.LONG,
        entry_price=5000.0,
        stop_price=4995.0,
        target_price=5007.5,
        strategy_name="orb",
        reason_codes=["test"],
    )
    decision = risk.size_trade(setup, setup.symbol)
    assert decision.approved is True
    assert decision.contracts > 0

    risk.register_trade_result(session, -500.0)
    risk.register_trade_result(session, -500.0)
    can_trade = risk.can_trade(session, "orb")
    assert can_trade.approved is False
