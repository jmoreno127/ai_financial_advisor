from __future__ import annotations

from advisor.ibkr.wrapper import IBKRState, MarketDataWrapper


class _FakeBar:
    def __init__(self) -> None:
        self.date = "20260304 12:30:00"
        self.open = 100.0
        self.high = 101.0
        self.low = 99.5
        self.close = 100.5
        self.volume = 250
        self.wap = 100.4
        self.barCount = 37


def test_historical_callbacks_aggregate_and_complete() -> None:
    state = IBKRState()
    wrapper = MarketDataWrapper(state)

    event = state.start_historical_request(5001, {"instrument_key": "MGC-202604-COMEX"})
    wrapper.historicalData(5001, _FakeBar())
    wrapper.historicalDataEnd(5001, "", "")

    assert event.is_set() is True
    bars, meta = state.consume_historical_request(5001)
    assert len(bars) == 1
    assert bars[0]["close"] == 100.5
    assert meta["instrument_key"] == "MGC-202604-COMEX"


def test_historical_error_marks_request_done() -> None:
    state = IBKRState()
    wrapper = MarketDataWrapper(state)
    event = state.start_historical_request(7001, {"instrument_key": "MNQ-202603-CME"})

    wrapper.error(7001, 162, "Historical market data Service error message")

    assert event.is_set() is True
    _, meta = state.consume_historical_request(7001)
    assert "162" in meta.get("error", "")


def test_non_fatal_historical_warning_does_not_complete_request() -> None:
    state = IBKRState()
    wrapper = MarketDataWrapper(state)
    event = state.start_historical_request(9001, {"instrument_key": "MES-202603-CME"})

    wrapper.error(9001, 2174, "Warning: You submitted request with date-time without time zone")

    assert event.is_set() is False
