from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List

from dotenv import load_dotenv


@dataclass(slots=True)
class AppConfig:
    openai_api_key: str
    openai_model_light: str
    openai_model_deep: str
    ibkr_host: str
    ibkr_port: int
    ibkr_client_id: int
    ibkr_account_id: str
    postgres_dsn: str
    run_interval_seconds: int
    watchlist: List[str]
    scanner_max_results: int
    trigger_move_pct: float
    trigger_pnl_delta_pct: float
    trigger_zscore: float
    max_margin_utilization: float
    max_single_name_exposure: float
    max_gross_leverage: float
    max_drawdown_from_day_high: float
    json_log_path: str

    @classmethod
    def from_env(cls) -> "AppConfig":
        load_dotenv()

        return cls(
            openai_api_key=_env_str("OPENAI_API_KEY", default=""),
            openai_model_light=_env_str("OPENAI_MODEL_LIGHT", "gpt-5-mini"),
            openai_model_deep=_env_str("OPENAI_MODEL_DEEP", "gpt-5"),
            ibkr_host=_env_str("IBKR_HOST", "127.0.0.1"),
            ibkr_port=_env_int("IBKR_PORT", 7496),
            ibkr_client_id=_env_int("IBKR_CLIENT_ID", 11),
            ibkr_account_id=_env_str("IBKR_ACCOUNT_ID", default=""),
            postgres_dsn=_env_str(
                "POSTGRES_DSN", "postgresql+psycopg://user:pass@localhost:5432/ai_advisor"
            ),
            run_interval_seconds=_env_int("RUN_INTERVAL_SECONDS", 60),
            watchlist=_env_list("WATCHLIST", "AAPL,MSFT,NVDA,TSLA,SPY,QQQ"),
            scanner_max_results=_env_int("SCANNER_MAX_RESULTS", 20),
            trigger_move_pct=_env_float("TRIGGER_MOVE_PCT", 1.2),
            trigger_pnl_delta_pct=_env_float("TRIGGER_PNL_DELTA_PCT", 0.8),
            trigger_zscore=_env_float("TRIGGER_ZSCORE", 2.0),
            max_margin_utilization=_env_float("MAX_MARGIN_UTILIZATION", 0.68),
            max_single_name_exposure=_env_float("MAX_SINGLE_NAME_EXPOSURE", 0.22),
            max_gross_leverage=_env_float("MAX_GROSS_LEVERAGE", 2.2),
            max_drawdown_from_day_high=_env_float("MAX_DRAWDOWN_FROM_DAY_HIGH", 0.04),
            json_log_path=_env_str("JSON_LOG_PATH", "logs/decisions.jsonl"),
        )

    @property
    def openai_enabled(self) -> bool:
        return bool(self.openai_api_key.strip())


def _env_str(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _env_list(key: str, default_csv: str) -> List[str]:
    raw = os.getenv(key, default_csv)
    return [item.strip() for item in raw.split(",") if item.strip()]
