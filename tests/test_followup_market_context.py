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


def test_extract_requested_instruments_filters_noise() -> None:
    known = ["MGC-202604-COMEX", "MNQ-202603-CME", "MES-202603-CME"]
    question = (
        "Should I close MGC:202604:COMEX and hedge MNQ:202603:CME now? "
        "What about randomword?"
    )

    extracted = extract_requested_instruments(question, known)

    assert extracted == ["MGC-202604-COMEX", "MNQ-202603-CME"]


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


def test_build_followup_market_context_contains_window_metrics() -> None:
    now = datetime(2026, 3, 4, 12, 0, tzinfo=timezone.utc)
    symbol = "MGC-202604-COMEX"
    points = [
        {"cycle_ts": now - timedelta(hours=6), "last_price": 100.0, "volume": 10.0, "pct_change": 0.2, "source": "watchlist"},
        {"cycle_ts": now - timedelta(hours=4), "last_price": 105.0, "volume": 20.0, "pct_change": 0.8, "source": "watchlist"},
        {"cycle_ts": now - timedelta(hours=2), "last_price": 102.0, "volume": 30.0, "pct_change": 0.4, "source": "watchlist"},
        {"cycle_ts": now - timedelta(minutes=30), "last_price": 108.0, "volume": 40.0, "pct_change": 1.1, "source": "watchlist"},
    ]

    context = build_followup_market_context(
        question="Reasons to buy/sell for MGC:202604:COMEX?",
        known_symbols=[symbol],
        history_by_symbol={symbol: points},
        now=now,
    )

    metrics = context["metrics_by_symbol"][symbol]
    window_5h = metrics["windows"]["5h"]
    assert metrics["status"] == "ok"
    assert window_5h["status"] == "ok"
    assert window_5h["sample_count"] == 3
    assert round(window_5h["vwap"], 3) == 105.333
    assert round(window_5h["return_pct"], 3) == 2.857
    assert round(window_5h["max_drawdown_pct"], 3) == 2.857
    assert round(window_5h["price_vs_vwap_pct"], 3) == 2.532
