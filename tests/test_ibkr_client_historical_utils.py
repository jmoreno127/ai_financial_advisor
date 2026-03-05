from __future__ import annotations

from advisor.ibkr.client import _contract_from_watchlist_entry, _parse_historical_bar_ts


def test_contract_from_watchlist_entry_accepts_canonical_futures_key() -> None:
    key, contract = _contract_from_watchlist_entry("MES-202603-CME")
    assert key == "MES-202603-CME"
    assert contract is None or getattr(contract, "secType", "FUT") == "FUT"


def test_parse_historical_bar_ts_handles_ibkr_formats() -> None:
    dt_1 = _parse_historical_bar_ts("20260304 12:30:00")
    dt_2 = _parse_historical_bar_ts("20260304")
    dt_3 = _parse_historical_bar_ts("1710000000")
    assert dt_1 is not None
    assert dt_2 is not None
    assert dt_3 is not None
