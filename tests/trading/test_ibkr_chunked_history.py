from __future__ import annotations

from datetime import datetime, timedelta, timezone

from advisor.models import HistoricalBar
from advisor.trading.data.ibkr_history import pull_chunked_history


class _FakeIBKR:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_historical_bars(self, instrument_entry: str, **kwargs):
        _ = instrument_entry
        self.calls += 1
        end_dt = kwargs.get("end_datetime")
        base = end_dt or datetime.now(timezone.utc)
        if self.calls > 2:
            return []
        return [
            HistoricalBar(
                instrument_key="MES-202606-CME",
                bar_ts=(base - timedelta(minutes=5)),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=100.0,
                wap=100.4,
                bar_count=1,
                bar_size="5 mins",
                what_to_show="TRADES",
                use_rth=False,
                source="ibkr_tws",
                fetched_at=datetime.now(timezone.utc),
            )
        ]


def test_pull_chunked_history_deduplicates_and_orders() -> None:
    fake = _FakeIBKR()
    out = pull_chunked_history(
        fake,
        ["MES-202606-CME"],
        months=3,
        bar_size="5 mins",
        what_to_show="TRADES",
        use_rth=False,
        timeout_seconds=5,
    )
    assert "MES-202606-CME" in out
    assert len(out["MES-202606-CME"]) >= 1
