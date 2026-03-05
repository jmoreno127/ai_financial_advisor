from __future__ import annotations

from datetime import datetime, timezone

import advisor.main as main_mod
from advisor.models import HistoricalBar


class _DummyLogger:
    def __init__(self) -> None:
        self.errors: list[dict] = []

    def error(self, message: str, **kwargs: object) -> None:
        self.errors.append({"message": message, **kwargs})


class _DummyStore:
    def __init__(self) -> None:
        self.upserts: list[HistoricalBar] = []
        self.prune_arg: int | None = None

    def upsert_historical_bars(self, bars: list[HistoricalBar]) -> None:
        self.upserts.extend(bars)

    def prune_historical_bars(self, retention_days: int) -> int:
        self.prune_arg = retention_days
        return 0


class _DummyConfig:
    ibkr_hist_duration = "8 D"
    ibkr_hist_bar_size = "5 mins"
    ibkr_hist_what_to_show = "TRADES"
    ibkr_hist_use_rth = False
    ibkr_hist_timeout_seconds = 20
    hist_cache_retention_days = 30


def test_refresh_historical_cache_success(monkeypatch) -> None:
    now = datetime.now(timezone.utc)

    class _FakeIBKRClient:
        def __init__(self, config) -> None:
            _ = config

        def start(self, subscribe_core: bool = True, subscribe_watchlist: bool = True) -> None:
            _ = (subscribe_core, subscribe_watchlist)

        def fetch_historical_bars(self, instrument_entry: str, **kwargs: object) -> list[HistoricalBar]:
            _ = kwargs
            return [
                HistoricalBar(
                    instrument_key="MGC-202604-COMEX",
                    bar_ts=now,
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=1.0,
                    volume=1.0,
                    wap=1.0,
                    bar_count=1,
                    bar_size="5 mins",
                    what_to_show="TRADES",
                    use_rth=False,
                    source="ibkr_tws",
                    fetched_at=now,
                )
            ]

        def stop(self) -> None:
            return None

    monkeypatch.setattr(main_mod, "IBKRClient", _FakeIBKRClient)
    store = _DummyStore()
    logger = _DummyLogger()
    failures = main_mod._refresh_historical_cache_for_symbols(
        config=_DummyConfig(),
        logger=logger,
        store=store,
        symbols=["MGC-202604-COMEX"],
        symbol_entry_map={"MGC-202604-COMEX": "MGC:202604:COMEX"},
    )
    assert failures == {}
    assert len(store.upserts) == 1
    assert store.prune_arg == 30


def test_refresh_historical_cache_connection_failure(monkeypatch) -> None:
    class _FakeIBKRClient:
        def __init__(self, config) -> None:
            _ = config

        def start(self, subscribe_core: bool = True, subscribe_watchlist: bool = True) -> None:
            _ = (subscribe_core, subscribe_watchlist)
            raise RuntimeError("connect fail")

    monkeypatch.setattr(main_mod, "IBKRClient", _FakeIBKRClient)
    store = _DummyStore()
    logger = _DummyLogger()
    failures = main_mod._refresh_historical_cache_for_symbols(
        config=_DummyConfig(),
        logger=logger,
        store=store,
        symbols=["MNQ-202603-CME"],
        symbol_entry_map={},
    )
    assert "MNQ-202603-CME" in failures
    assert store.prune_arg is None


def test_refresh_historical_cache_no_bars(monkeypatch) -> None:
    class _FakeIBKRClient:
        def __init__(self, config) -> None:
            _ = config

        def start(self, subscribe_core: bool = True, subscribe_watchlist: bool = True) -> None:
            _ = (subscribe_core, subscribe_watchlist)

        def fetch_historical_bars(self, instrument_entry: str, **kwargs: object) -> list[HistoricalBar]:
            _ = (instrument_entry, kwargs)
            return []

        def stop(self) -> None:
            return None

    monkeypatch.setattr(main_mod, "IBKRClient", _FakeIBKRClient)
    store = _DummyStore()
    logger = _DummyLogger()
    failures = main_mod._refresh_historical_cache_for_symbols(
        config=_DummyConfig(),
        logger=logger,
        store=store,
        symbols=["MES-202603-CME"],
        symbol_entry_map={},
    )
    assert "MES-202603-CME" in failures
    assert store.prune_arg == 30
