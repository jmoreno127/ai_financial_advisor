from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from advisor.config import AppConfig
from advisor.ibkr.client import IBKRClient
from advisor.output.logger import StructuredLogger
from advisor.storage.postgres import PostgresStore
from advisor.trading.config import TradingConfig, load_trading_config
from advisor.trading.data.ibkr_history import pull_chunked_history
from advisor.trading.data.loader import add_common_features, filter_session_scope, normalize_bars
from advisor.trading.execution.simulator import ExecutionSimulator
from advisor.trading.paper.engine import PaperRuntime
from advisor.trading.paper.kill_switch import set_kill_switch
from advisor.trading.reporting.analytics import compute_metrics, rank_score
from advisor.trading.reporting.io import load_validation_output, write_trade_outputs, write_validation_output
from advisor.trading.risk.engine import RiskEngine
from advisor.trading.strategies.base import BaseStrategy, StrategyContext
from advisor.trading.strategies.orb import ORBParams, ORBStrategy
from advisor.trading.strategies.vwap_pullback import VWAPParams, VWAPPullbackStrategy
from advisor.trading.types import BacktestReport, PositionState, SignalAction, ValidationResult


@dataclass(slots=True)
class TradingRuntimeContext:
    app_config: AppConfig
    trading_config: TradingConfig
    logger: StructuredLogger
    store: PostgresStore


def load_runtime_context(app_config: AppConfig, config_path: str) -> TradingRuntimeContext:
    trading_cfg = load_trading_config(config_path)
    logger = StructuredLogger(app_config.json_log_path)
    store = PostgresStore(app_config.postgres_dsn)
    return TradingRuntimeContext(app_config=app_config, trading_config=trading_cfg, logger=logger, store=store)


def run_backtest(ctx: TradingRuntimeContext) -> dict:
    market_data = _load_market_data(ctx)
    candidates = _strategy_candidates(ctx.trading_config)

    reports: List[BacktestReport] = []
    for variant_name, strategy in candidates:
        report = _run_backtest_for_strategy(ctx.trading_config, strategy, variant_name, market_data)
        reports.append(report)
        write_trade_outputs(ctx.trading_config.runtime.output_dir, variant_name, report.trades, report.equity_curve)
        ctx.store.write_trading_event(
            event_type="backtest_completed",
            symbol="ALL",
            strategy=strategy.name,
            payload={"variant": variant_name, "metrics": report.metrics},
        )

    ranked = sorted(reports, key=lambda r: rank_score(r.metrics), reverse=True)
    best = ranked[0] if ranked else None
    return {
        "reports": [
            {
                "strategy": report.strategy_name,
                "variant": report.variant_name,
                "metrics": report.metrics,
            }
            for report in ranked
        ],
        "best": {
            "strategy": best.strategy_name,
            "variant": best.variant_name,
            "metrics": best.metrics,
        }
        if best
        else None,
    }


def run_validation(ctx: TradingRuntimeContext) -> ValidationResult:
    market_data = _load_market_data(ctx)
    candidates = _strategy_candidates(ctx.trading_config)

    results: List[ValidationResult] = []
    for variant_name, strategy in candidates:
        result = _validate_strategy(ctx.trading_config, strategy, variant_name, market_data)
        results.append(result)
        ctx.store.write_trading_event(
            event_type="validation_completed",
            symbol="ALL",
            strategy=strategy.name,
            payload={
                "variant": variant_name,
                "passed": result.passed,
                "score": result.score,
                "oos_profit_factor": result.oos_profit_factor,
            },
        )

    passing = [item for item in results if item.passed]
    if passing:
        best = sorted(passing, key=lambda r: r.score, reverse=True)[0]
    else:
        best = sorted(results, key=lambda r: r.score, reverse=True)[0]

    write_validation_output(ctx.trading_config.runtime.output_dir, best)
    return best


def run_paper(ctx: TradingRuntimeContext) -> None:
    validation = load_validation_output(ctx.trading_config.runtime.output_dir)
    if not validation or not bool(validation.get("passed")):
        raise RuntimeError("No passing validation result found. Run `advisor validate --config <yaml>` first.")

    strategy = _strategy_from_validation(ctx.trading_config, str(validation.get("variant_name", "")))
    if strategy is None:
        raise RuntimeError("Unable to map validation result to strategy variant.")

    runtime = PaperRuntime(
        app_config=ctx.app_config,
        trading_config=ctx.trading_config,
        strategy=strategy,
        store=ctx.store,
        logger=ctx.logger,
    )
    runtime.run()


def set_paper_kill_switch(ctx: TradingRuntimeContext, enabled: bool) -> dict:
    set_kill_switch(ctx.store, enabled)
    ctx.store.write_trading_event(
        event_type="kill_switch_set",
        symbol="ALL",
        strategy="system",
        payload={"enabled": enabled},
    )
    return {"kill_switch": enabled}


def _load_market_data(ctx: TradingRuntimeContext) -> Dict[str, pd.DataFrame]:
    def _ibkr_error_handler(payload: dict) -> None:
        code = payload.get("error_code", "")
        msg = payload.get("error_string", "")
        ctx.logger.error(
            f"IBKR error: {code} - {msg}",
            error_code=code,
            error_string=msg,
        )

    client = IBKRClient(ctx.app_config, error_handler=_ibkr_error_handler)
    client.start(subscribe_core=False, subscribe_watchlist=False)
    try:
        symbols = [_canonical_symbol(entry) for entry in ctx.trading_config.universe.watchlist]
        raw = pull_chunked_history(
            client,
            symbols,
            months=ctx.trading_config.universe.backtest_months,
            bar_size=ctx.trading_config.universe.primary_bar_size,
            what_to_show=ctx.app_config.ibkr_hist_what_to_show,
            use_rth=False,
            timeout_seconds=ctx.app_config.ibkr_hist_timeout_seconds,
        )
    finally:
        client.stop()

    out: Dict[str, pd.DataFrame] = {}
    for symbol, df in raw.items():
        if df.empty:
            out[symbol] = df
            continue
        data = normalize_bars(df)
        data = add_common_features(data)
        data = filter_session_scope(data, ctx.trading_config.universe.session_scope)
        out[symbol] = data
    return out


def _strategy_candidates(config: TradingConfig) -> List[Tuple[str, BaseStrategy]]:
    candidates: List[Tuple[str, BaseStrategy]] = []

    if config.strategies.orb.enabled:
        for minutes in config.strategies.orb.opening_range_minutes:
            params = ORBParams(
                opening_range_minutes=int(minutes),
                min_range_points=config.strategies.orb.min_range_points,
                max_range_points=config.strategies.orb.max_range_points,
                target_r_multiple=config.strategies.orb.target_r_multiple,
                one_trade_per_day=config.strategies.orb.one_trade_per_day,
            )
            candidates.append((f"orb_{minutes}m", ORBStrategy(params)))

    if config.strategies.vwap_pullback.enabled:
        v_params = VWAPParams(
            pullback_band_atr_mult=config.strategies.vwap_pullback.pullback_band_atr_mult,
            target_r_multiple=config.strategies.vwap_pullback.target_r_multiple,
        )
        candidates.append(("vwap_pullback", VWAPPullbackStrategy(v_params)))

    return candidates


def _strategy_from_validation(config: TradingConfig, variant: str) -> BaseStrategy | None:
    for name, strategy in _strategy_candidates(config):
        if name == variant:
            return strategy
    return None


def _run_backtest_for_strategy(
    config: TradingConfig,
    strategy: BaseStrategy,
    variant_name: str,
    market_data: Dict[str, pd.DataFrame],
) -> BacktestReport:
    prepared: Dict[str, pd.DataFrame] = {}
    for symbol, data in market_data.items():
        if data.empty:
            prepared[symbol] = data
            continue
        base = data
        if "trade_date" not in base.columns or "atr" not in base.columns or "vwap" not in base.columns:
            base = normalize_bars(base)
            base = add_common_features(base)
            base = filter_session_scope(base, config.universe.session_scope)
        prepared[symbol] = strategy.prepare_features(base)

    timeline = _timeline(prepared)
    positions: Dict[str, PositionState] = {}
    sim = ExecutionSimulator(config)
    risk = RiskEngine(config, equity=config.account.starting_equity)
    now = datetime.now(timezone.utc)
    session = risk.build_session_state(now)
    trades = []
    equity_curve = [config.account.starting_equity]

    for ts, symbol, idx in timeline:
        df = prepared[symbol]
        row = df.iloc[idx]
        risk.reset_if_needed(session, ts)

        position = positions.get(symbol)
        if position is not None:
            force_flat = _is_force_flat(ts, config.execution.force_flat_time)
            fill = sim.process_bar(
                position,
                bar_ts=ts,
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                force_flat=force_flat,
            )
            if fill is not None:
                trades.append(fill)
                risk.register_trade_result(session, fill.realized_pnl_net)
                equity_curve.append(risk.equity)
                del positions[symbol]
            continue

        allowed = risk.can_trade(session, strategy.name)
        if not allowed.approved:
            continue

        context = StrategyContext(symbol=symbol, index=idx, data=df, state={})
        signal = strategy.generate_signal(context)
        if signal.action != SignalAction.ENTRY or signal.setup is None:
            continue

        sizing = risk.size_trade(signal.setup, symbol)
        if not sizing.approved:
            continue

        risk.register_entry(session, strategy.name)
        positions[symbol] = PositionState(
            symbol=symbol,
            side=signal.setup.side,
            contracts=sizing.contracts,
            entry_price=sim.apply_slippage(signal.setup.entry_price, signal.setup.side, entry=True, symbol=symbol),
            stop_price=signal.setup.stop_price,
            target_price=signal.setup.target_price,
            opened_at=ts,
            strategy_name=strategy.name,
            reason_codes=signal.setup.reason_codes,
        )

    metrics = compute_metrics(trades, equity_curve)
    drawdown_curve = _drawdown_curve(equity_curve)
    return BacktestReport(
        strategy_name=strategy.name,
        variant_name=variant_name,
        metrics=metrics,
        trades=trades,
        equity_curve=equity_curve,
        drawdown_curve=drawdown_curve,
    )


def _validate_strategy(
    config: TradingConfig,
    strategy: BaseStrategy,
    variant_name: str,
    market_data: Dict[str, pd.DataFrame],
) -> ValidationResult:
    windows = max(2, int(config.validation.walk_forward_windows))
    timestamps = _all_timestamps(market_data)
    if len(timestamps) < 200:
        return ValidationResult(
            passed=False,
            strategy_name=strategy.name,
            variant_name=variant_name,
            score=0.0,
            oos_profit_factor=0.0,
            oos_max_drawdown=1.0,
            oos_trades=0,
            oos_expectancy=0.0,
            details={"reason": "insufficient_data"},
        )

    oos_reports: List[BacktestReport] = []
    n = len(timestamps)
    train = max(80, int(n * 0.55))
    oos = max(80, int(n * 0.25))
    step = max(20, int((n - train - oos) / max(windows - 1, 1)))

    for i in range(windows):
        start_idx = i * step
        oos_start = start_idx + train
        oos_end = min(oos_start + oos, n)
        if oos_end - oos_start < 20:
            continue
        start_ts = timestamps[oos_start]
        end_ts = timestamps[oos_end - 1]

        sliced = {}
        for symbol, df in market_data.items():
            if df.empty:
                sliced[symbol] = df
            else:
                sliced[symbol] = df[(df["timestamp"] >= start_ts) & (df["timestamp"] <= end_ts)].copy()

        report = _run_backtest_for_strategy(config, strategy, variant_name, sliced)
        oos_reports.append(report)

    all_trades = [trade for report in oos_reports for trade in report.trades]
    full_equity = [config.account.starting_equity]
    for report in oos_reports:
        for point in report.equity_curve[1:]:
            full_equity.append(point)

    metrics = compute_metrics(all_trades, full_equity)
    score = rank_score(metrics)
    passed = (
        metrics.get("profit_factor", 0.0) >= config.validation.min_oos_profit_factor
        and metrics.get("max_drawdown", 1.0) <= config.validation.max_oos_drawdown
        and metrics.get("trades", 0.0) >= config.validation.min_oos_trades
        and metrics.get("expectancy", -1.0) > config.validation.min_oos_expectancy
    )

    return ValidationResult(
        passed=passed,
        strategy_name=strategy.name,
        variant_name=variant_name,
        score=score,
        oos_profit_factor=float(metrics.get("profit_factor", 0.0)),
        oos_max_drawdown=float(metrics.get("max_drawdown", 1.0)),
        oos_trades=int(metrics.get("trades", 0.0)),
        oos_expectancy=float(metrics.get("expectancy", 0.0)),
        details={"windows": len(oos_reports), "metrics": metrics},
    )


def _timeline(data_by_symbol: Dict[str, pd.DataFrame]) -> List[Tuple[datetime, str, int]]:
    timeline: List[Tuple[datetime, str, int]] = []
    for symbol, data in data_by_symbol.items():
        for idx, row in data.iterrows():
            timeline.append((row["timestamp"], symbol, idx))
    timeline.sort(key=lambda item: item[0])
    return timeline


def _all_timestamps(data_by_symbol: Dict[str, pd.DataFrame]) -> List[datetime]:
    values = []
    for df in data_by_symbol.values():
        if df.empty:
            continue
        values.extend(df["timestamp"].tolist())
    uniq = sorted(set(values))
    return uniq


def _drawdown_curve(equity_curve: List[float]) -> List[float]:
    if not equity_curve:
        return []
    max_seen = equity_curve[0]
    drawdowns: List[float] = []
    for point in equity_curve:
        if point > max_seen:
            max_seen = point
        if max_seen <= 0:
            drawdowns.append(0.0)
        else:
            drawdowns.append((max_seen - point) / max_seen)
    return drawdowns


def _canonical_symbol(entry: str) -> str:
    text = entry.strip()
    if not text:
        return text
    if text.count("-") == 2:
        return text.upper()

    parts = [part.strip() for part in text.split(":") if part.strip()]
    if len(parts) >= 4 and parts[0].upper() == "FUT":
        return f"{parts[1].upper()}-{parts[2]}-{parts[3].upper()}"
    if len(parts) >= 3 and parts[1].isdigit():
        return f"{parts[0].upper()}-{parts[1]}-{parts[2].upper()}"
    return text.upper()


def _is_force_flat(ts: datetime, hhmmss: str) -> bool:
    try:
        h, m, s = [int(item) for item in hhmmss.split(":")]
    except Exception:
        h, m, s = 16, 55, 0
    if isinstance(ts, pd.Timestamp):
        et = ts.tz_convert("America/New_York")
    else:
        et = ts.astimezone(ZoneInfo("America/New_York"))
    return (et.hour, et.minute, et.second) >= (h, m, s)
