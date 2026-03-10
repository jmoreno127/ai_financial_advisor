from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Tuple

from advisor.config import AppConfig
from advisor.ibkr.scanner import build_most_active_subscription, build_top_movers_subscription
from advisor.ibkr.wrapper import IBKRState, MarketDataWrapper
from advisor.models import HistoricalBar, InstrumentSnapshot, PortfolioSnapshot, PositionSnapshot

try:
    from ibapi.client import EClient
    from ibapi.contract import Contract
    from ibapi.order import Order
except Exception:  # pragma: no cover - optional dependency import guard
    EClient = None  # type: ignore[assignment]
    Contract = None  # type: ignore[assignment]
    Order = None  # type: ignore[assignment]


class IBKRClient:
    def __init__(
        self,
        config: AppConfig,
        error_handler: Callable[[Dict[str, Any]], None] | None = None,
    ):
        self.config = config
        self.state = IBKRState()
        self.wrapper = MarketDataWrapper(self.state, error_handler=error_handler)
        self._day_high_equity = 0.0

        self._thread: threading.Thread | None = None
        self._running = False
        self._next_req_id = 1000
        self._scanner_req_ids = [7001, 7002]
        self._active_scanner_req_ids: set[int] = set()

        if EClient is None:
            self.client = None
        else:
            self.client = EClient(self.wrapper)

    def start(self, subscribe_core: bool = True, subscribe_watchlist: bool = True) -> None:
        if self.client is None:
            raise RuntimeError("ibapi is not installed. Install dependencies with `pip install -e .`.")

        self.client.connect(self.config.ibkr_host, self.config.ibkr_port, self.config.ibkr_client_id)
        self._thread = threading.Thread(target=self.client.run, daemon=True)
        self._thread.start()
        self._running = True

        if not self.wrapper.connected_event.wait(timeout=10):
            raise RuntimeError("IBKR connection timeout. Verify TWS/IB Gateway host/port/API settings.")

        if subscribe_core:
            self.refresh_core_subscriptions()
        if subscribe_watchlist:
            self.ensure_market_data_subscriptions(self.config.watchlist)

    def stop(self) -> None:
        if self.client is None:
            return
        self._running = False
        self._active_scanner_req_ids.clear()
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

        for watchlist_entry in symbols:
            key, contract = _contract_from_watchlist_entry(watchlist_entry)
            if not key or contract is None:
                continue

            with self.state.lock:
                existing_id = self.state.symbol_to_ticker.get(key)
            if existing_id is not None:
                continue

            ticker_id = self._next_request_id()
            self.state.register_ticker(key, ticker_id)
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
        for req_id in list(self._active_scanner_req_ids):
            try:
                self.client.cancelScannerSubscription(req_id)
            except Exception:
                pass
        self._active_scanner_req_ids.clear()

        for req_id, sub in zip(self._scanner_req_ids, subs):
            if sub is None:
                continue
            self.client.reqScannerSubscription(req_id, sub, [], [])
            self._active_scanner_req_ids.add(req_id)

    def readiness_status(self) -> Dict[str, Any]:
        snapshot = self.state.snapshot()
        account_values = snapshot["account_values"]
        ticker_values = snapshot["ticker_values"]
        positions = snapshot["positions"]
        required_account_tags = ("NetLiquidation", "InitMarginReq", "ExcessLiquidity")

        account_ready = all(tag in account_values for tag in required_account_tags)
        positions_ready = self.wrapper.positions_ready_event.is_set()
        market_data_ready = self.wrapper.market_data_event.is_set()

        non_empty_tickers = 0
        for values in ticker_values.values():
            if values.get("last") is not None or values.get("prev_close") is not None:
                non_empty_tickers += 1

        return {
            "connected": self.is_connected(),
            "account_ready": account_ready,
            "positions_ready": positions_ready,
            "market_data_ready": market_data_ready,
            "account_tags_received": sorted(account_values.keys()),
            "positions_count": len(positions),
            "tickers_with_data": non_empty_tickers,
            "watchlist_size": len(self.config.watchlist),
            "scanner_symbols": len(snapshot["scanner_symbols"]),
        }

    def wait_for_initial_data(
        self,
        timeout_seconds: int = 60,
        progress_interval_seconds: int = 10,
        progress_callback: Callable[[Dict[str, Any]], None] | None = None,
    ) -> bool:
        start = time.monotonic()
        last_progress = -progress_interval_seconds

        while True:
            elapsed = int(time.monotonic() - start)
            status = self.readiness_status()
            ready = status["account_ready"] and status["positions_ready"]
            if ready:
                return True

            if elapsed - last_progress >= progress_interval_seconds:
                last_progress = elapsed
                if progress_callback is not None:
                    progress_callback(
                        {
                            "elapsed_seconds": elapsed,
                            "timeout_seconds": timeout_seconds,
                            **status,
                        }
                    )

            if elapsed >= timeout_seconds:
                if progress_callback is not None:
                    progress_callback(
                        {
                            "elapsed_seconds": elapsed,
                            "timeout_seconds": timeout_seconds,
                            "timed_out": True,
                            **status,
                        }
                    )
                return False

            time.sleep(1)

    def scanner_symbols(self) -> List[str]:
        snapshot = self.state.snapshot()
        return list(snapshot["scanner_symbols"].keys())[: self.config.scanner_max_results]

    def order_events(self) -> List[Dict[str, Any]]:
        snapshot = self.state.snapshot()
        return list(snapshot.get("order_events", []))

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

    def fetch_historical_bars(
        self,
        instrument_entry: str,
        *,
        duration: str,
        bar_size: str,
        what_to_show: str,
        use_rth: bool,
        timeout_seconds: int,
        end_datetime: datetime | None = None,
    ) -> List[HistoricalBar]:
        if self.client is None:
            raise RuntimeError("ibapi is not installed. Install dependencies with `pip install -e .`.")
        if not self.is_connected():
            raise RuntimeError("IBKR is not connected")

        instrument_key, contract = _contract_from_watchlist_entry(instrument_entry)
        if not instrument_key or contract is None:
            raise ValueError(f"Unsupported instrument format: {instrument_entry}")

        req_id = self._next_request_id()
        done_event = self.state.start_historical_request(
            req_id,
            {
                "instrument_key": instrument_key,
                "instrument_entry": instrument_entry,
                "duration": duration,
                "bar_size": bar_size,
                "what_to_show": what_to_show,
                "use_rth": use_rth,
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        completed = False
        try:
            end_dt = ""
            if end_datetime is not None:
                end_dt = end_datetime.astimezone(timezone.utc).strftime("%Y%m%d-%H:%M:%S UTC")
            self.client.reqHistoricalData(
                req_id,
                contract,
                end_dt,
                duration,
                bar_size,
                what_to_show,
                1 if use_rth else 0,
                1,
                False,
                [],
            )
            completed = done_event.wait(timeout=max(1, timeout_seconds))
            if not completed:
                self.state.complete_historical_request(req_id, error="timeout")
                raise TimeoutError(f"Historical data timeout for {instrument_key}")
        finally:
            if not completed:
                try:
                    self.client.cancelHistoricalData(req_id)
                except Exception:
                    pass

        bars_raw, meta = self.state.consume_historical_request(req_id)
        error = meta.get("error")
        if error:
            raise RuntimeError(f"Historical data error for {instrument_key}: {error}")

        fetched_at = datetime.now(timezone.utc)
        results: List[HistoricalBar] = []
        for bar in bars_raw:
            bar_ts = _parse_historical_bar_ts(bar.get("date"))
            if bar_ts is None:
                continue
            results.append(
                HistoricalBar(
                    instrument_key=instrument_key,
                    bar_ts=bar_ts,
                    open=_to_float(bar.get("open")),
                    high=_to_float(bar.get("high")),
                    low=_to_float(bar.get("low")),
                    close=_to_float(bar.get("close")),
                    volume=_to_float(bar.get("volume")),
                    wap=_to_float(bar.get("wap")),
                    bar_count=int(_to_float(bar.get("bar_count"))),
                    bar_size=bar_size,
                    what_to_show=what_to_show,
                    use_rth=use_rth,
                    source="ibkr_tws",
                    fetched_at=fetched_at,
                )
            )
        return results

    def place_order(
        self,
        *,
        instrument_entry: str,
        action: str,
        quantity: int,
        order_type: str = "MKT",
        aux_price: float | None = None,
        lmt_price: float | None = None,
        tif: str = "DAY",
        transmit: bool = True,
    ) -> int:
        if self.client is None:
            raise RuntimeError("ibapi is not installed. Install dependencies with `pip install -e .`.")
        if not self.is_connected():
            raise RuntimeError("IBKR is not connected")
        if Contract is None or Order is None:
            raise RuntimeError("ibapi contract/order types unavailable")

        _, contract = _contract_from_watchlist_entry(instrument_entry)
        if contract is None:
            raise ValueError(f"Unsupported instrument format: {instrument_entry}")

        order = Order()
        order.action = action.upper()
        order.totalQuantity = int(quantity)
        order.orderType = order_type.upper()
        order.tif = tif
        order.transmit = bool(transmit)
        if aux_price is not None:
            order.auxPrice = float(aux_price)
        if lmt_price is not None:
            order.lmtPrice = float(lmt_price)

        order_id = self._next_order_id()
        self.client.placeOrder(order_id, contract, order)
        return order_id

    def _next_request_id(self) -> int:
        self._next_req_id += 1
        return self._next_req_id

    def _next_order_id(self) -> int:
        with self.state.lock:
            next_valid = self.state.next_valid_order_id
            if next_valid is None:
                self._next_req_id += 1
                return self._next_req_id
            self.state.next_valid_order_id = next_valid + 1
            return next_valid


def _stock_contract(symbol: str):
    if Contract is None:
        return None
    contract = Contract()
    contract.symbol = symbol
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.currency = "USD"
    return contract


def _futures_contract(symbol: str, expiry: str, exchange: str, currency: str = "USD"):
    if Contract is None:
        return None
    contract = Contract()
    contract.symbol = symbol
    contract.secType = "FUT"
    contract.lastTradeDateOrContractMonth = expiry
    contract.exchange = exchange
    contract.currency = currency
    return contract


def _contract_from_watchlist_entry(entry: str):
    """
    Supported formats:
    - STK symbol only: AAPL
    - Explicit stock: STK:AAPL[:SMART[:USD]]
    - Futures explicit: FUT:GC:202606|20260628:COMEX[:USD]
    - Futures shorthand: GC:202606|20260628:COMEX[:USD]
    - Futures canonical key: GC-202606|20260628-COMEX
    """
    text = entry.strip()
    if not text:
        return "", None

    dash_parts = [part.strip() for part in text.split("-") if part.strip()]
    if len(dash_parts) == 3 and dash_parts[1].isdigit() and len(dash_parts[1]) in (6, 8):
        symbol = dash_parts[0].upper()
        expiry = dash_parts[1]
        exchange = dash_parts[2].upper()
        key = f"{symbol}-{expiry}-{exchange}"
        return key, _futures_contract(symbol, expiry, exchange, "USD")

    parts = [part.strip() for part in text.split(":") if part.strip()]
    if not parts:
        return "", None

    head = parts[0].upper()

    if head == "STK":
        if len(parts) < 2:
            return "", None
        symbol = parts[1].upper()
        exchange = parts[2].upper() if len(parts) >= 3 else "SMART"
        currency = parts[3].upper() if len(parts) >= 4 else "USD"
        contract = _stock_contract(symbol)
        if contract is None:
            return "", None
        contract.exchange = exchange
        contract.currency = currency
        return symbol, contract

    if head == "FUT":
        if len(parts) < 4:
            return "", None
        symbol = parts[1].upper()
        expiry = parts[2]
        exchange = parts[3].upper()
        currency = parts[4].upper() if len(parts) >= 5 else "USD"
        key = f"{symbol}-{expiry}-{exchange}"
        return key, _futures_contract(symbol, expiry, exchange, currency)

    # FUT shorthand: SYMBOL:YYYYMM:EXCHANGE[:CURRENCY]
    if len(parts) >= 3 and parts[1].isdigit() and len(parts[1]) in (6, 8):
        symbol = parts[0].upper()
        expiry = parts[1]
        exchange = parts[2].upper()
        currency = parts[3].upper() if len(parts) >= 4 else "USD"
        key = f"{symbol}-{expiry}-{exchange}"
        return key, _futures_contract(symbol, expiry, exchange, currency)

    symbol = text.upper()
    return symbol, _stock_contract(symbol)


def _parse_historical_bar_ts(raw: Any) -> datetime | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    if text.isdigit():
        try:
            if len(text) >= 10:
                return datetime.fromtimestamp(int(text), tz=timezone.utc)
        except Exception:
            return None

    candidates = [
        "%Y%m%d %H:%M:%S",
        "%Y%m%d  %H:%M:%S",
        "%Y%m%d-%H:%M:%S",
        "%Y%m%d",
    ]
    base_text = text
    if " " in text and text.count(" ") >= 2:
        parts = text.split()
        base_text = f"{parts[0]} {parts[1]}"

    for fmt in candidates:
        try:
            parsed = datetime.strptime(base_text, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
