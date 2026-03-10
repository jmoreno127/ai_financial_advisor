from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from advisor.storage.postgres import PostgresStore
from advisor.trading.paper.kill_switch import is_kill_switch_on, set_kill_switch


def _store(tmp_path: Path) -> PostgresStore:
    db_path = tmp_path / "controls.sqlite3"
    store = PostgresStore(f"sqlite+pysqlite:///{db_path}")
    store._schema_initialized = True
    with store.engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS trading_controls (
                    control_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
    return store


def test_kill_switch_db_toggle(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert is_kill_switch_on(store) is False
    set_kill_switch(store, True)
    assert is_kill_switch_on(store) is True
    set_kill_switch(store, False)
    assert is_kill_switch_on(store) is False
