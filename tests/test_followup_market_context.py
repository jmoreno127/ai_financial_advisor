from __future__ import annotations

from datetime import datetime, timedelta, timezone

from advisor.engine.followup_market_context import (
    build_followup_market_context,
    canonical_instrument_key,
    extract_requested_instruments,
)


def test_canonical_instrument_key_formats() -> None:
    assert canonical_instrument_key("MGC:202604:COMEX") == "MGC-202604-COMEX"
    assert canonical_instrument_key("FUT:MNQ:202603:CME:USD") == "MNQ-202603-CME"
    assert canonical_instrument_key("STK:SPY:SMART:USD") == "SPY"
    assert canonical_instrument_key("MES-202603-CME") == "MES-202603-CME"
    assert canonical_instrument_key("aapl") == "AAPL"
    assert canonical_instrument_key("invalid token") is None


def test_extract_requested_instruments_for_futures_list() -> None:
    known: list[str] = []
    question = (
        "Analyze reasons to buy/sell MGC:202604:COMEX,MNQ:202603:CME,"
        "MES:202603:CME,MYM:202603:CBOT,M2K:202603:CME"
    )

    extracted = extract_requested_instruments(question, known)

    assert extracted == [
        "MGC-202604-COMEX",
        "MNQ-202603-CME",
        "MES-202603-CME",
        "MYM-202603-CBOT",
        "M2K-202603-CME",
    ]


def test_build_followup_market_context_prefers_historical_cache() -> None:
    now = datetime(2026, 3, 4, 12, 0, tzinfo=timezone.utc)
    symbol = "MGC-202604-COMEX"
    bars = [
        {"cycle_ts": now - timedelta(hours=6), "open": 99.0, "high": 101.0, "low": 98.5, "last_price": 100.0, "volume": 10.0, "source": "ibkr_tws"},
        {"cycle_ts": now - timedelta(hours=4), "open": 100.0, "high": 106.0, "low": 99.0, "last_price": 105.0, "volume": 20.0, "source": "ibkr_tws"},
        {"cycle_ts": now - timedelta(hours=2), "open": 104.0, "high": 105.0, "low": 101.0, "last_price": 102.0, "volume": 30.0, "source": "ibkr_tws"},
        {"cycle_ts": now - timedelta(minutes=30), "open": 103.0, "high": 109.0, "low": 102.0, "last_price": 108.0, "volume": 40.0, "source": "ibkr_tws"},
    ]

    context = build_followup_market_context(
        question="Reasons to buy/sell for MGC:202604:COMEX?",
        known_symbols=[symbol],
        history_by_symbol={symbol: bars},
        now=now,
    )

    metrics = context["metrics_by_symbol"][symbol]
    window_5h = metrics["windows"]["5h"]
    assert metrics["status"] == "ok"
    assert metrics["data_source"] == "ibkr_historical_cache"
    assert metrics["data_quality"] == "fresh"
    assert window_5h["status"] == "ok"
    assert window_5h["sample_count"] == 3
    assert round(window_5h["vwap"], 3) == 105.333
    assert round(window_5h["return_pct"], 3) == 2.857
    assert round(window_5h["max_drawdown_pct"], 3) == 2.857
    assert round(window_5h["price_vs_vwap_pct"], 3) == 2.532
    assert context["source_summary"]["ibkr_historical_cache"] == 1
    assert context["source_summary"]["fresh"] == 1


def test_build_followup_market_context_uses_snapshot_fallback() -> None:
    now = datetime(2026, 3, 4, 12, 0, tzinfo=timezone.utc)
    symbol = "MES-202603-CME"
    fallback_points = [
        {"cycle_ts": now - timedelta(hours=2), "last_price": 5012.0, "volume": 1000.0, "pct_change": 0.1, "source": "watchlist"},
        {"cycle_ts": now - timedelta(hours=1), "last_price": 5020.0, "volume": 1200.0, "pct_change": 0.2, "source": "watchlist"},
    ]

    context = build_followup_market_context(
        question="Should I close MES:202603:CME?",
        known_symbols=[symbol],
        history_by_symbol={},
        snapshot_fallback_by_symbol={symbol: fallback_points},
        fetch_failures={symbol: "Historical request timeout"},
        now=now,
    )

    metrics = context["metrics_by_symbol"][symbol]
    assert metrics["status"] == "ok"
    assert metrics["data_source"] == "snapshot_fallback"
    assert metrics["data_quality"] == "fallback"
    assert metrics["fallback_reason"] == "Historical request timeout"
    assert context["source_summary"]["snapshot_fallback"] == 1
    assert context["source_summary"]["fallback"] == 1
