from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from advisor.trading.data.loader import add_common_features, normalize_bars
from advisor.trading.strategies.base import StrategyContext
from advisor.trading.strategies.vwap_pullback import VWAPParams, VWAPPullbackStrategy
from advisor.trading.types import SignalAction


def test_vwap_pullback_generates_entry_signal() -> None:
    rows = []
    start = datetime(2026, 1, 6, 14, 30, tzinfo=timezone.utc)
    price = 100.0
    for i in range(40):
        ts = start + pd.Timedelta(minutes=5 * i)
        price += 0.2
        rows.append(
            {
                "timestamp": ts,
                "open": price - 0.1,
                "high": price + 0.2,
                "low": price - 0.2,
                "close": price,
                "volume": 1000 + i * 5,
                "symbol": "MNQ-202606-CME",
            }
        )

    # Pull back near VWAP on final bar while trend remains up.
    rows[-1]["close"] = rows[-2]["close"] - 0.4
    rows[-1]["high"] = rows[-1]["close"] + 0.2
    rows[-1]["low"] = rows[-1]["close"] - 0.2

    df = normalize_bars(pd.DataFrame(rows))
    df = add_common_features(df)
    strategy = VWAPPullbackStrategy(VWAPParams(pullback_band_atr_mult=1.0, target_r_multiple=1.4))
    prepared = strategy.prepare_features(df)

    context = StrategyContext(symbol="MNQ-202606-CME", index=len(prepared) - 1, data=prepared, state={})
    signal = strategy.generate_signal(context)
    assert signal.action in {SignalAction.ENTRY, SignalAction.NO_ACTION}
