from __future__ import annotations

import os

from advisor.storage.postgres import PostgresStore


def env_kill_switch_on() -> bool:
    raw = os.getenv("TRADING_KILL_SWITCH", "false").strip().lower()
    return raw in {"1", "true", "yes", "on", "y"}


def is_kill_switch_on(store: PostgresStore) -> bool:
    return env_kill_switch_on() or store.get_trading_kill_switch()


def set_kill_switch(store: PostgresStore, enabled: bool) -> None:
    store.set_trading_kill_switch(enabled)
