from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict
from zoneinfo import ZoneInfo

import pandas as pd

from advisor.config import AppConfig
from advisor.ibkr.client import IBKRClient
from advisor.output.logger import StructuredLogger
from advisor.storage.postgres import PostgresStore
from advisor.trading.config import TradingConfig
from advisor.trading.data.loader import add_common_features, filter_session_scope, normalize_bars
from advisor.trading.execution.simulator import ExecutionSimulator
from advisor.trading.paper.broker import BrokerAdapter, IBKRPaperBrokerAdapter
from advisor.trading.paper.kill_switch import is_kill_switch_on
from advisor.trading.portfolio.state import load_positions, save_positions
from advisor.trading.risk.engine import RiskEngine
from advisor.trading.strategies.base import BaseStrategy, StrategyContext
from advisor.trading.strategies.conflicts import choose_best_signal
from advisor.trading.types import OrderIntent, PositionState, SignalAction


@dataclass(slots=True)
class PaperRuntime:
    app_config: AppConfig
    trading_config: TradingConfig
    strategy: BaseStrategy
    store: PostgresStore
    logger: StructuredLogger
    broker: BrokerAdapter | None = None

    def run(self) -> None:
        managed_client: IBKRClient | None = None
        if self.broker is None:
            ibkr_client = IBKRClient(
                self.app_config,
                error_handler=lambda payload: self.logger.error("IBKR error", **payload),
            )
            ibkr_client.start(subscribe_core=False, subscribe_watchlist=False)
            self.broker = IBKRPaperBrokerAdapter(ibkr_client)
            managed_client = ibkr_client
        try:
            self._run_loop()
        finally:
            if managed_client is not None:
                managed_client.stop()

    def _run_loop(self) -> None:
        positions = load_positions(self.trading_config.runtime.state_file)
        risk = RiskEngine(self.trading_config, equity=self.trading_config.account.starting_equity)
        now = datetime.now(timezone.utc)
        session = risk.build_session_state(now)
        sim = ExecutionSimulator(self.trading_config)

        while True:
            now = datetime.now(timezone.utc)
            risk.reset_if_needed(session, now)
            bars = self._poll_recent_bars()

            for symbol, df in bars.items():
                if df.empty:
                    continue

                latest = df.iloc[-1]
                position = positions.get(symbol)
                if position is not None:
                    force_flat = _is_force_flat(now, self.trading_config.execution.force_flat_time)
                    fill = sim.process_bar(
                        position,
                        bar_ts=latest["timestamp"],
                        high=float(latest["high"]),
                        low=float(latest["low"]),
                        close=float(latest["close"]),
                        force_flat=force_flat,
                    )
                    if fill:
                        del positions[symbol]
                        risk.register_trade_result(session, fill.realized_pnl_net)
                        self.store.write_trading_event(
                            event_type="paper_exit",
                            symbol=symbol,
                            strategy=fill.strategy_name,
                            payload={
                                "exit_reason": fill.exit_reason.value,
                                "pnl_net": fill.realized_pnl_net,
                                "contracts": fill.contracts,
                            },
                        )
                    continue

                if is_kill_switch_on(self.store):
                    self.store.write_trading_event(
                        event_type="paper_entry_blocked",
                        symbol=symbol,
                        strategy=self.strategy.name,
                        payload={"reason": "kill_switch"},
                    )
                    continue

                permission = risk.can_trade(session, self.strategy.name)
                if not permission.approved:
                    self.store.write_trading_event(
                        event_type="paper_entry_blocked",
                        symbol=symbol,
                        strategy=self.strategy.name,
                        payload={"reason": permission.reason},
                    )
                    continue

                context = StrategyContext(symbol=symbol, index=len(df) - 1, data=df, state={})
                signal = self.strategy.generate_signal(context)
                if signal.action != SignalAction.ENTRY or signal.setup is None:
                    continue

                setup = choose_best_signal([signal.setup])
                if setup is None:
                    continue

                sized = risk.size_trade(setup, symbol)
                if not sized.approved:
                    self.store.write_trading_event(
                        event_type="paper_entry_blocked",
                        symbol=symbol,
                        strategy=self.strategy.name,
                        payload={"reason": sized.reason},
                    )
                    continue

                order = OrderIntent(
                    symbol=symbol,
                    timestamp=now,
                    side=setup.side,
                    contracts=sized.contracts,
                    entry_price=setup.entry_price,
                    stop_price=setup.stop_price,
                    target_price=setup.target_price,
                    strategy_name=setup.strategy_name,
                )
                order_id = self.broker.place_entry(order)
                risk.register_entry(session, self.strategy.name)

                positions[symbol] = PositionState(
                    symbol=symbol,
                    side=setup.side,
                    contracts=sized.contracts,
                    entry_price=sim.apply_slippage(setup.entry_price, setup.side, entry=True, symbol=symbol),
                    stop_price=setup.stop_price,
                    target_price=setup.target_price,
                    opened_at=now,
                    strategy_name=setup.strategy_name,
                    reason_codes=setup.reason_codes,
                )
                self.store.write_trading_event(
                    event_type="paper_entry",
                    symbol=symbol,
                    strategy=self.strategy.name,
                    payload={
                        "order_id": order_id,
                        "contracts": sized.contracts,
                        "entry_price": setup.entry_price,
                    },
                )

            save_positions(self.trading_config.runtime.state_file, positions)
            time.sleep(max(1, int(self.trading_config.runtime.poll_seconds)))

    def _poll_recent_bars(self) -> Dict[str, pd.DataFrame]:
        watch_map = {_canonical_symbol(entry): entry for entry in self.trading_config.universe.watchlist}
        symbols = list(watch_map.keys())

        if isinstance(self.broker, IBKRPaperBrokerAdapter):
            out: Dict[str, pd.DataFrame] = {}
            for symbol in symbols:
                entry = watch_map[symbol]
                try:
                    bars = self.broker.ibkr.fetch_historical_bars(
                        entry,
                        duration="2 D",
                        bar_size=self.trading_config.universe.primary_bar_size,
                        what_to_show="TRADES",
                        use_rth=False,
                        timeout_seconds=max(5, int(self.app_config.ibkr_hist_timeout_seconds)),
                    )
                except Exception as exc:
                    self.logger.error("Paper bar poll failed", symbol=symbol, error=str(exc))
                    out[symbol] = pd.DataFrame()
                    continue

                df = pd.DataFrame(
                    {
                        "timestamp": [bar.bar_ts for bar in bars],
                        "open": [bar.open for bar in bars],
                        "high": [bar.high for bar in bars],
                        "low": [bar.low for bar in bars],
                        "close": [bar.close for bar in bars],
                        "volume": [bar.volume for bar in bars],
                        "symbol": [bar.instrument_key for bar in bars],
                    }
                )
                if df.empty:
                    out[symbol] = df
                    continue
                prepared = normalize_bars(df)
                prepared = add_common_features(prepared)
                prepared = filter_session_scope(prepared, self.trading_config.universe.session_scope)
                prepared = self.strategy.prepare_features(prepared)
                out[symbol] = prepared
            return out

        since = datetime.now(timezone.utc) - pd.Timedelta(days=2)
        rows = self.store.historical_bars(
            symbols=symbols,
            since_ts=since,
            bar_size=self.trading_config.universe.primary_bar_size,
            what_to_show="TRADES",
            use_rth=False,
        )
        out: Dict[str, pd.DataFrame] = {}
        for symbol, items in rows.items():
            df = pd.DataFrame(
                {
                    "timestamp": [row["cycle_ts"] for row in items],
                    "open": [row["open"] for row in items],
                    "high": [row["high"] for row in items],
                    "low": [row["low"] for row in items],
                    "close": [row["last_price"] for row in items],
                    "volume": [row["volume"] for row in items],
                    "symbol": [symbol] * len(items),
                }
            )
            if df.empty:
                out[symbol] = df
                continue
            prepared = normalize_bars(df)
            prepared = add_common_features(prepared)
            prepared = filter_session_scope(prepared, self.trading_config.universe.session_scope)
            prepared = self.strategy.prepare_features(prepared)
            out[symbol] = prepared
        return out


def _canonical_symbol(watchlist_entry: str) -> str:
    text = watchlist_entry.strip()
    if not text:
        return text
    if "-" in text and text.count("-") == 2:
        return text

    parts = [p.strip() for p in text.split(":") if p.strip()]
    if len(parts) >= 4 and parts[0].upper() == "FUT":
        return f"{parts[1].upper()}-{parts[2]}-{parts[3].upper()}"
    if len(parts) >= 3 and parts[1].isdigit():
        return f"{parts[0].upper()}-{parts[1]}-{parts[2].upper()}"
    return text.upper()


def _is_force_flat(ts_utc: datetime, hhmmss: str) -> bool:
    try:
        hh, mm, ss = [int(x) for x in hhmmss.split(":")]
    except Exception:
        hh, mm, ss = 16, 55, 0
    local = ts_utc.astimezone(ZoneInfo("America/New_York"))
    return (local.hour, local.minute, local.second) >= (hh, mm, ss)
