from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Tuple

from advisor.config import AppConfig
from advisor.ibkr.scanner import build_most_active_subscription, build_top_movers_subscription
from advisor.ibkr.wrapper import IBKRState, MarketDataWrapper
from advisor.models import InstrumentSnapshot, PortfolioSnapshot, PositionSnapshot

try:
    from ibapi.client import EClient
    from ibapi.contract import Contract
except Exception:  # pragma: no cover - optional dependency import guard
    EClient = None  # type: ignore[assignment]
    Contract = None  # type: ignore[assignment]


class IBKRClient:
    def __init__(self, config: AppConfig):
        self.config = config
        self.state = IBKRState()
        self.wrapper = MarketDataWrapper(self.state)
        self._day_high_equity = 0.0

        self._thread: threading.Thread | None = None
        self._running = False
        self._next_req_id = 1000
        self._scanner_req_ids = [7001, 7002]

        if EClient is None:
            self.client = None
        else:
            self.client = EClient(self.wrapper)

    def start(self) -> None:
        if self.client is None:
            raise RuntimeError("ibapi is not installed. Install dependencies with `pip install -e .`.")

        self.client.connect(self.config.ibkr_host, self.config.ibkr_port, self.config.ibkr_client_id)
        self._thread = threading.Thread(target=self.client.run, daemon=True)
        self._thread.start()
        self._running = True

        if not self.wrapper.connected_event.wait(timeout=10):
            raise RuntimeError("IBKR connection timeout. Verify TWS/IB Gateway host/port/API settings.")

        self.refresh_core_subscriptions()
        self.ensure_market_data_subscriptions(self.config.watchlist)

    def stop(self) -> None:
        if self.client is None:
            return
        self._running = False
        try:
            self.client.disconnect()
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def is_connected(self) -> bool:
        return bool(self.client and self.client.isConnected())

    def refresh_core_subscriptions(self) -> None:
        if self.client is None:
            return

        self.client.reqAccountSummary(
            9001,
            "All",
            "NetLiquidation,InitMarginReq,ExcessLiquidity,DailyPnL,UnrealizedPnL,RealizedPnL",
        )
        self.client.reqPositions()
        self.client.reqAccountUpdates(True, self.config.ibkr_account_id)

        if self.config.ibkr_account_id:
            try:
                self.client.reqPnL(9002, self.config.ibkr_account_id, "")
            except Exception:
                pass

    def ensure_market_data_subscriptions(self, symbols: Iterable[str]) -> None:
        if self.client is None or Contract is None:
            return

        for raw_symbol in symbols:
            symbol = raw_symbol.upper().strip()
            if not symbol:
                continue

            with self.state.lock:
                existing_id = self.state.symbol_to_ticker.get(symbol)
            if existing_id is not None:
                continue

            ticker_id = self._next_request_id()
            contract = _stock_contract(symbol)
            self.state.register_ticker(symbol, ticker_id)
            self.client.reqMktData(ticker_id, contract, "", False, False, [])

    def request_scanner_refresh(self) -> None:
        if self.client is None:
            return

        with self.state.lock:
            self.state.scanner_symbols.clear()

        subs = [
            build_top_movers_subscription(self.config.scanner_max_results),
            build_most_active_subscription(self.config.scanner_max_results),
        ]
        for req_id in self._scanner_req_ids:
            try:
                self.client.cancelScannerSubscription(req_id)
            except Exception:
                pass

        for req_id, sub in zip(self._scanner_req_ids, subs):
            if sub is None:
                continue
            self.client.reqScannerSubscription(req_id, sub, [], [])

    def scanner_symbols(self) -> List[str]:
        snapshot = self.state.snapshot()
        return list(snapshot["scanner_symbols"].keys())[: self.config.scanner_max_results]

    def collect_snapshot(self, cycle_ts: datetime) -> Tuple[PortfolioSnapshot, List[InstrumentSnapshot]]:
        snapshot = self.state.snapshot()
        account_values: Dict[str, float] = snapshot["account_values"]
        position_map: Dict[str, PositionSnapshot] = snapshot["positions"]
        ticker_values: Dict[int, Dict[str, float]] = snapshot["ticker_values"]
        ticker_to_symbol: Dict[int, str] = snapshot["ticker_to_symbol"]

        positions = list(position_map.values())
        gross_position_value = sum(abs(position.market_value) for position in positions)

        net_liquidation = account_values.get("NetLiquidation", 0.0)
        init_margin_req = account_values.get("InitMarginReq", 0.0)
        excess_liquidity = account_values.get("ExcessLiquidity", 0.0)
        daily_pnl = account_values.get("DailyPnL", 0.0)
        total_unrealized = account_values.get("UnrealizedPnL", 0.0)

        if net_liquidation > self._day_high_equity:
            self._day_high_equity = net_liquidation

        instruments: List[InstrumentSnapshot] = []
        for ticker_id, values in ticker_values.items():
            symbol = ticker_to_symbol.get(ticker_id)
            if not symbol:
                continue
            last_price = values.get("last", 0.0)
            prev_close = values.get("prev_close", 0.0)
            pct_change = 0.0 if prev_close == 0 else ((last_price - prev_close) / prev_close) * 100.0
            volume = values.get("volume", 0.0)
            source = "scanner" if symbol in snapshot["scanner_symbols"] else "watchlist"
            instruments.append(
                InstrumentSnapshot(
                    symbol=symbol,
                    last_price=last_price,
                    previous_close=prev_close,
                    pct_change=pct_change,
                    volume=volume,
                    source=source,
                    timestamp=cycle_ts,
                )
            )

        portfolio = PortfolioSnapshot(
            cycle_ts=cycle_ts,
            account_id=self.config.ibkr_account_id or "UNKNOWN",
            net_liquidation=net_liquidation,
            init_margin_req=init_margin_req,
            excess_liquidity=excess_liquidity,
            gross_position_value=gross_position_value,
            daily_pnl=daily_pnl,
            total_unrealized_pnl=total_unrealized,
            day_high_equity=self._day_high_equity,
            positions=positions,
        )
        return portfolio, instruments

    def reconnect_if_needed(self) -> bool:
        if self.client is None:
            return False
        if self.is_connected():
            return True

        for _ in range(3):
            try:
                self.stop()
                time.sleep(1)
                self.start()
                return True
            except Exception:
                time.sleep(2)
        return False

    def _next_request_id(self) -> int:
        self._next_req_id += 1
        return self._next_req_id


def _stock_contract(symbol: str):
    if Contract is None:
        return None
    contract = Contract()
    contract.symbol = symbol
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.currency = "USD"
    return contract
