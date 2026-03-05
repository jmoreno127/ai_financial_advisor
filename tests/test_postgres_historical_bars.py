from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

from advisor.models import HistoricalBar
from advisor.storage.postgres import PostgresStore


def _store(tmp_path: Path) -> PostgresStore:
    db_path = tmp_path / "hist_cache.sqlite3"
    store = PostgresStore(f"sqlite+pysqlite:///{db_path}")
    store._schema_initialized = True
    with store.engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS instrument_historical_bars (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    instrument_key TEXT NOT NULL,
                    bar_ts TIMESTAMP NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    wap REAL NOT NULL,
                    bar_count INTEGER NOT NULL,
                    bar_size TEXT NOT NULL,
                    what_to_show TEXT NOT NULL,
                    use_rth BOOLEAN NOT NULL,
                    source TEXT NOT NULL,
                    fetched_at TIMESTAMP NOT NULL,
                    UNIQUE (instrument_key, bar_ts, bar_size, what_to_show, use_rth)
                )
                """
            )
        )
    return store


def test_upsert_and_query_historical_bars(tmp_path: Path) -> None:
    store = _store(tmp_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    bar_a = HistoricalBar(
        instrument_key="MGC-202604-COMEX",
        bar_ts=now - timedelta(minutes=10),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000.0,
        wap=100.3,
        bar_count=12,
        bar_size="5 mins",
        what_to_show="TRADES",
        use_rth=False,
        source="ibkr_tws",
        fetched_at=now,
    )
    bar_b = bar_a.model_copy(update={"close": 101.0})

    store.upsert_historical_bars([bar_a, bar_b])
    rows = store.historical_bars(
        symbols=["MGC-202604-COMEX"],
        since_ts=now - timedelta(days=1),
        bar_size="5 mins",
        what_to_show="TRADES",
        use_rth=False,
    )
    assert len(rows["MGC-202604-COMEX"]) == 1
    assert rows["MGC-202604-COMEX"][0]["last_price"] == 101.0


def test_prune_historical_bars(tmp_path: Path) -> None:
    store = _store(tmp_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    old_bar = HistoricalBar(
        instrument_key="MNQ-202603-CME",
        bar_ts=now - timedelta(days=40),
        open=10.0,
        high=11.0,
        low=9.0,
        close=10.0,
        volume=100.0,
        wap=10.0,
        bar_count=1,
        bar_size="5 mins",
        what_to_show="TRADES",
        use_rth=False,
        source="ibkr_tws",
        fetched_at=now,
    )
    new_bar = old_bar.model_copy(update={"bar_ts": now - timedelta(days=2)})
    store.upsert_historical_bars([old_bar, new_bar])
    deleted = store.prune_historical_bars(30)
    rows = store.historical_bars(
        symbols=["MNQ-202603-CME"],
        since_ts=now - timedelta(days=60),
        bar_size="5 mins",
        what_to_show="TRADES",
        use_rth=False,
    )

    assert deleted == 1
    assert len(rows["MNQ-202603-CME"]) == 1
