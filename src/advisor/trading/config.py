from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml


RISK_PROFILES: Dict[str, Dict[str, float | int]] = {
    "conservative": {
        "risk_per_trade_pct": 0.003,
        "max_daily_loss_pct": 0.012,
        "max_weekly_loss_pct": 0.03,
        "max_trades_per_day_per_strategy": 2,
        "max_consecutive_losses": 2,
    },
    "moderate": {
        "risk_per_trade_pct": 0.005,
        "max_daily_loss_pct": 0.018,
        "max_weekly_loss_pct": 0.045,
        "max_trades_per_day_per_strategy": 3,
        "max_consecutive_losses": 3,
    },
    "aggressive": {
        "risk_per_trade_pct": 0.0075,
        "max_daily_loss_pct": 0.025,
        "max_weekly_loss_pct": 0.06,
        "max_trades_per_day_per_strategy": 4,
        "max_consecutive_losses": 4,
    },
}


INSTRUMENT_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "MES": {
        "point_value": 5.0,
        "tick_size": 0.25,
        "tick_value": 1.25,
    },
    "MNQ": {
        "point_value": 2.0,
        "tick_size": 0.25,
        "tick_value": 0.5,
    },
    "M2K": {
        "point_value": 5.0,
        "tick_size": 0.1,
        "tick_value": 0.5,
    },
    "MYM": {
        "point_value": 0.5,
        "tick_size": 1.0,
        "tick_value": 0.5,
    },
    "MGC": {
        "point_value": 10.0,
        "tick_size": 0.1,
        "tick_value": 1.0,
    },
    "SI": {
        "point_value": 5000.0,
        "tick_size": 0.005,
        "tick_value": 25.0,
    },
}


@dataclass(slots=True)
class AccountConfig:
    starting_equity: float = 57000.0
    risk_profile: str = "conservative"


@dataclass(slots=True)
class ExecutionConfig:
    entry_order_type: str = "STOP_MARKET"
    slippage_ticks: int = 1
    commission_per_side: float = 0.85
    same_bar_resolution: str = "pessimistic"
    force_flat_time: str = "16:55:00"


@dataclass(slots=True)
class UniverseConfig:
    watchlist: List[str] = field(
        default_factory=lambda: ["MGC:202604:COMEX", "MNQ:202606:CME", "MES:202606:CME", "SI:202605:COMEX"]
    )
    primary_bar_size: str = "5 mins"
    session_scope: List[str] = field(default_factory=lambda: ["RTH", "ETH"])
    backtest_months: int = 12


@dataclass(slots=True)
class OrbConfig:
    enabled: bool = True
    opening_range_minutes: List[int] = field(default_factory=lambda: [15, 30])
    min_range_points: float = 2.0
    max_range_points: float = 50.0
    target_r_multiple: float = 1.5
    one_trade_per_day: bool = True


@dataclass(slots=True)
class VwapConfig:
    enabled: bool = True
    pullback_band_atr_mult: float = 0.35
    target_r_multiple: float = 1.4


@dataclass(slots=True)
class StrategyConfig:
    orb: OrbConfig = field(default_factory=OrbConfig)
    vwap_pullback: VwapConfig = field(default_factory=VwapConfig)


@dataclass(slots=True)
class ValidationConfig:
    min_oos_profit_factor: float = 1.2
    max_oos_drawdown: float = 0.08
    min_oos_trades: int = 100
    min_oos_expectancy: float = 0.0
    walk_forward_windows: int = 4


@dataclass(slots=True)
class RuntimeConfig:
    poll_seconds: int = 60
    output_dir: str = "outputs"
    state_file: str = "outputs/state/paper_state.json"


@dataclass(slots=True)
class TradingConfig:
    account: AccountConfig = field(default_factory=AccountConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    strategies: StrategyConfig = field(default_factory=StrategyConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    instrument_specs: Dict[str, Dict[str, Any]] = field(default_factory=lambda: dict(INSTRUMENT_DEFAULTS))

    @property
    def active_risk_profile(self) -> Dict[str, float | int]:
        profile = self.account.risk_profile.lower().strip()
        return RISK_PROFILES.get(profile, RISK_PROFILES["conservative"])


def load_trading_config(path: str | Path) -> TradingConfig:
    payload: Dict[str, Any] = {}
    config_path = Path(path)
    if config_path.exists():
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    merged = _merge_defaults(payload)
    cfg = TradingConfig(
        account=AccountConfig(**merged["account"]),
        execution=ExecutionConfig(**merged["execution"]),
        universe=UniverseConfig(**merged["universe"]),
        strategies=StrategyConfig(
            orb=OrbConfig(**merged["strategies"]["orb"]),
            vwap_pullback=VwapConfig(**merged["strategies"]["vwap_pullback"]),
        ),
        validation=ValidationConfig(**merged["validation"]),
        runtime=RuntimeConfig(**merged["runtime"]),
        instrument_specs=merged["instrument_specs"],
    )
    _apply_env_overrides(cfg)
    return cfg


def _merge_defaults(payload: Dict[str, Any]) -> Dict[str, Any]:
    default = {
        "account": asdict(AccountConfig()),
        "execution": asdict(ExecutionConfig()),
        "universe": asdict(UniverseConfig()),
        "strategies": {
            "orb": asdict(OrbConfig()),
            "vwap_pullback": asdict(VwapConfig()),
        },
        "validation": asdict(ValidationConfig()),
        "runtime": asdict(RuntimeConfig()),
        "instrument_specs": dict(INSTRUMENT_DEFAULTS),
    }

    result = default
    _deep_update(result, payload)
    return result


def _deep_update(base: Dict[str, Any], update: Dict[str, Any]) -> None:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value


def _apply_env_overrides(config: TradingConfig) -> None:
    risk_profile = os.getenv("TRADING_RISK_PROFILE", "").strip().lower()
    if risk_profile in RISK_PROFILES:
        config.account.risk_profile = risk_profile

    starting_equity = os.getenv("TRADING_STARTING_EQUITY", "").strip()
    if starting_equity:
        config.account.starting_equity = float(starting_equity)

    watchlist = os.getenv("TRADING_WATCHLIST", "").strip()
    if watchlist:
        config.universe.watchlist = [item.strip() for item in watchlist.split(",") if item.strip()]

    backtest_months = os.getenv("TRADING_BACKTEST_MONTHS", "").strip()
    if backtest_months:
        config.universe.backtest_months = max(1, int(backtest_months))
