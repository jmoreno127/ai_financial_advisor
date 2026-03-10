from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from advisor.config import AppConfig
from advisor.trading.service import load_runtime_context, run_backtest, run_paper, run_validation


def _write_config(path: Path) -> None:
    path.write_text(
        """
account:
  starting_equity: 57000
  risk_profile: conservative
universe:
  watchlist: [MES:202606:CME]
  backtest_months: 1
runtime:
  output_dir: outputs
  state_file: outputs/state/paper_state.json
""",
        encoding="utf-8",
    )


def test_backtest_validate_and_paper_flow(monkeypatch, tmp_path: Path) -> None:
    cfg_path = tmp_path / "trading.yaml"
    _write_config(cfg_path)

    app_cfg = AppConfig.from_env()
    app_cfg.postgres_dsn = f"sqlite+pysqlite:///{tmp_path / 'service.sqlite3'}"
    app_cfg.json_log_path = str(tmp_path / "decisions.jsonl")

    ctx = load_runtime_context(app_cfg, str(cfg_path))
    ctx.store._schema_initialized = True
    with ctx.store.engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS trading_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    event_type TEXT NOT NULL,
                    symbol TEXT,
                    strategy TEXT,
                    payload TEXT NOT NULL
                )
                """
            )
        )
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

    bars = pd.DataFrame(
        [
            {
                "timestamp": datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc) + pd.Timedelta(minutes=5 * i),
                "open": 100 + i * 0.1,
                "high": 100.2 + i * 0.1,
                "low": 99.8 + i * 0.1,
                "close": 100.1 + i * 0.1,
                "volume": 1000 + i,
                "symbol": "MES-202606-CME",
            }
            for i in range(300)
        ]
    )

    monkeypatch.setattr(
        "advisor.trading.service._load_market_data",
        lambda _ctx: {"MES-202606-CME": bars.copy()},
    )

    backtest = run_backtest(ctx)
    assert backtest["best"] is not None

    validation = run_validation(ctx)
    assert validation.variant_name != ""

    monkeypatch.setattr("advisor.trading.service.load_validation_output", lambda _out: {"passed": True, "variant_name": validation.variant_name})

    called = {"ran": False}

    class _DummyRuntime:
        def __init__(self, **kwargs):
            _ = kwargs

        def run(self):
            called["ran"] = True

    monkeypatch.setattr("advisor.trading.service.PaperRuntime", _DummyRuntime)
    run_paper(ctx)
    assert called["ran"] is True
