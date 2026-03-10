from __future__ import annotations

from typing import Iterable, Optional

from advisor.trading.types import TradeSetup


def choose_best_signal(setups: Iterable[TradeSetup]) -> Optional[TradeSetup]:
    pool = list(setups)
    if not pool:
        return None

    # Expected R minus risk penalty is the default dynamic conflict score.
    pool.sort(key=lambda s: (s.expected_r - s.risk_penalty), reverse=True)
    return pool[0]
