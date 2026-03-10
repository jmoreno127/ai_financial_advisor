from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List

import pandas as pd

from advisor.ibkr.client import IBKRClient
from advisor.models import HistoricalBar


def pull_chunked_history(
    ibkr: IBKRClient,
    symbols: List[str],
    *,
    months: int,
    bar_size: str,
    what_to_show: str,
    use_rth: bool,
    timeout_seconds: int,
) -> Dict[str, pd.DataFrame]:
    result: Dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        bars: List[HistoricalBar] = []
        end_time = datetime.now(timezone.utc)
        for _ in range(max(1, months)):
            chunk = ibkr.fetch_historical_bars(
                symbol,
                duration="1 M",
                bar_size=bar_size,
                what_to_show=what_to_show,
                use_rth=use_rth,
                timeout_seconds=timeout_seconds,
                end_datetime=end_time,
            )
            if not chunk:
                break
            bars.extend(chunk)
            earliest = min(item.bar_ts for item in chunk)
            end_time = earliest - timedelta(seconds=1)

        if not bars:
            result[symbol] = pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume", "symbol"]
            )
            continue

        unique: Dict[datetime, HistoricalBar] = {}
        for bar in bars:
            unique[bar.bar_ts] = bar

        rows = [
            {
                "timestamp": ts,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "symbol": bar.instrument_key,
            }
            for ts, bar in sorted(unique.items(), key=lambda item: item[0])
        ]
        result[symbol] = pd.DataFrame(rows)
    return result
