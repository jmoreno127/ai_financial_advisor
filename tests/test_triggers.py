from datetime import datetime, timezone

from advisor.config import AppConfig
from advisor.engine.metrics import RollingWindowState
from advisor.engine.triggers import evaluate_triggers
from advisor.models import InstrumentSnapshot, PortfolioSnapshot


def _config() -> AppConfig:
    return AppConfig(
        openai_api_key="x",
        openai_model_light="gpt-5-mini",
        openai_model_deep="gpt-5",
        ibkr_host="127.0.0.1",
        ibkr_port=7496,
        ibkr_client_id=11,
        ibkr_account_id="DU1",
        postgres_dsn="postgresql+psycopg://u:p@localhost:5432/db",
        run_interval_seconds=60,
        watchlist=["AAPL"],
        scanner_max_results=20,
        trigger_move_pct=1.2,
        trigger_pnl_delta_pct=0.8,
        trigger_zscore=2.0,
        max_margin_utilization=0.68,
        max_single_name_exposure=0.22,
        max_gross_leverage=2.2,
        max_drawdown_from_day_high=0.04,
        json_log_path="logs/decisions.jsonl",
    )


def _portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        cycle_ts=datetime.now(timezone.utc),
        account_id="DU1",
        net_liquidation=100_000,
        init_margin_req=40_000,
        excess_liquidity=50_000,
        gross_position_value=120_000,
        daily_pnl=500,
        total_unrealized_pnl=300,
        day_high_equity=100_000,
        positions=[],
    )


def test_no_trigger_under_quiet_market() -> None:
    cfg = _config()
    rolling = RollingWindowState()
    portfolio = _portfolio()
    instruments = [
        InstrumentSnapshot(
            symbol="AAPL",
            last_price=100,
            previous_close=99.7,
            pct_change=0.30,
            volume=1_000_000,
            source="watchlist",
            timestamp=datetime.now(timezone.utc),
        )
    ]
    rolling.update(portfolio, instruments)
    events = evaluate_triggers(cfg, portfolio, instruments, rolling)
    assert events == []


def test_trigger_on_absolute_move() -> None:
    cfg = _config()
    rolling = RollingWindowState()
    portfolio = _portfolio()
    instruments = [
        InstrumentSnapshot(
            symbol="AAPL",
            last_price=102,
            previous_close=100,
            pct_change=2.0,
            volume=1_000_000,
            source="watchlist",
            timestamp=datetime.now(timezone.utc),
        )
    ]
    rolling.update(portfolio, instruments)
    events = evaluate_triggers(cfg, portfolio, instruments, rolling)
    assert any(event.name == "absolute_move" for event in events)


def test_trigger_on_zscore_anomaly() -> None:
    cfg = _config()
    rolling = RollingWindowState()
    portfolio = _portfolio()

    for _ in range(12):
        rolling.update(
            portfolio,
            [
                InstrumentSnapshot(
                    symbol="AAPL",
                    last_price=100,
                    previous_close=100,
                    pct_change=0.2,
                    volume=1_000_000,
                    source="watchlist",
                    timestamp=datetime.now(timezone.utc),
                )
            ],
        )

    anomalous = [
        InstrumentSnapshot(
            symbol="AAPL",
            last_price=107,
            previous_close=100,
            pct_change=7.0,
            volume=2_000_000,
            source="watchlist",
            timestamp=datetime.now(timezone.utc),
        )
    ]
    rolling.update(portfolio, anomalous)
    events = evaluate_triggers(cfg, portfolio, anomalous, rolling)
    assert any(event.name == "zscore_anomaly" for event in events)
