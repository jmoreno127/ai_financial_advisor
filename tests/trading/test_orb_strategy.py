from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from advisor.trading.data.loader import add_common_features, normalize_bars
from advisor.trading.strategies.base import StrategyContext
from advisor.trading.strategies.orb import ORBParams, ORBStrategy
from advisor.trading.types import SignalAction, Side


def test_orb_generates_breakout_long_signal() -> None:
    rows = []
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)  # 09:30 ET
    for i in range(10):
        ts = start + pd.Timedelta(minutes=5 * i)
        close = 100.0 + (0.1 * i)
        rows.append(
            {
                "timestamp": ts,
                "open": close - 0.1,
                "high": close + 0.3,
                "low": close - 0.3,
                "close": close,
                "volume": 1000 + i * 10,
                "symbol": "MES-202606-CME",
            }
        )

    # Force breakout after opening range window.
    rows[-1]["close"] = 110.0
    rows[-1]["high"] = 110.4

    df = normalize_bars(pd.DataFrame(rows))
    df = add_common_features(df)

    strategy = ORBStrategy(
        ORBParams(opening_range_minutes=15, min_range_points=0.2, max_range_points=100.0, target_r_multiple=1.5)
    )
    prepared = strategy.prepare_features(df)
    context = StrategyContext(symbol="MES-202606-CME", index=len(prepared) - 1, data=prepared, state={})

    signal = strategy.generate_signal(context)
    assert signal.action == SignalAction.ENTRY
    assert signal.setup is not None
    assert signal.setup.side == Side.LONG
    assert signal.setup.strategy_name == "orb"
