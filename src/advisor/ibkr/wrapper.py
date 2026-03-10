from __future__ import annotations

import threading
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from advisor.models import PositionSnapshot

try:
    from ibapi.wrapper import EWrapper
except Exception:  # pragma: no cover - optional dependency import guard
    class EWrapper:  # type: ignore[override]
        pass


LAST_PRICE_TICK = 4
PREV_CLOSE_TICK = 9
VOLUME_TICK = 8
IBKR_INFO_CODES = {2104, 2106, 2107, 2108, 2158, 365}
IBKR_WARNING_CODES = {2103, 2105, 2109, 2110}
IBKR_NON_FATAL_HISTORICAL_CODES = {2174, 2176}


@dataclass
class IBKRState:
    account_values: Dict[str, float] = field(default_factory=dict)
    positions: Dict[str, PositionSnapshot] = field(default_factory=dict)
    ticker_values: Dict[int, Dict[str, float]] = field(default_factory=dict)
    ticker_to_symbol: Dict[int, str] = field(default_factory=dict)
    symbol_to_ticker: Dict[str, int] = field(default_factory=dict)
    scanner_symbols: Dict[str, int] = field(default_factory=dict)
    ibkr_errors: List[Dict[str, Any]] = field(default_factory=list)
    historical_bars_by_req_id: Dict[int, List[Dict[str, Any]]] = field(default_factory=dict)
    historical_done_by_req_id: Dict[int, threading.Event] = field(default_factory=dict)
    historical_meta_by_req_id: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    next_valid_order_id: Optional[int] = None
    order_events: List[Dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def register_ticker(self, symbol: str, ticker_id: int) -> None:
        with self.lock:
            self.symbol_to_ticker[symbol] = ticker_id
            self.ticker_to_symbol[ticker_id] = symbol
            self.ticker_values.setdefault(ticker_id, {})

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "account_values": dict(self.account_values),
                "positions": {k: v.model_copy(deep=True) for k, v in self.positions.items()},
                "ticker_values": {k: dict(v) for k, v in self.ticker_values.items()},
                "ticker_to_symbol": dict(self.ticker_to_symbol),
                "scanner_symbols": dict(self.scanner_symbols),
                "ibkr_errors": list(self.ibkr_errors),
                "order_events": list(self.order_events),
            }

    def start_historical_request(self, req_id: int, meta: Dict[str, Any]) -> threading.Event:
        with self.lock:
            done_event = threading.Event()
            self.historical_bars_by_req_id[req_id] = []
            self.historical_done_by_req_id[req_id] = done_event
            self.historical_meta_by_req_id[req_id] = dict(meta)
            return done_event

    def append_historical_bar(self, req_id: int, bar_data: Dict[str, Any]) -> None:
        with self.lock:
            self.historical_bars_by_req_id.setdefault(req_id, []).append(bar_data)

    def complete_historical_request(self, req_id: int, error: str | None = None) -> None:
        with self.lock:
            done_event = self.historical_done_by_req_id.get(req_id)
            meta = self.historical_meta_by_req_id.get(req_id)
            if meta is not None:
                meta["completed_at"] = datetime.now(timezone.utc).isoformat()
                if error:
                    meta["error"] = error
            if done_event is not None:
                done_event.set()

    def get_historical_done_event(self, req_id: int) -> Optional[threading.Event]:
        with self.lock:
            return self.historical_done_by_req_id.get(req_id)

    def consume_historical_request(self, req_id: int) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        with self.lock:
            bars = self.historical_bars_by_req_id.pop(req_id, [])
            meta = self.historical_meta_by_req_id.pop(req_id, {})
            self.historical_done_by_req_id.pop(req_id, None)
            return bars, meta


class MarketDataWrapper(EWrapper):
    def __init__(
        self,
        state: IBKRState,
        error_handler: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        super().__init__()
        self.state = state
        self.error_handler = error_handler
        self.connected_event = threading.Event()
        self.account_summary_event = threading.Event()
        self.positions_ready_event = threading.Event()
        self.market_data_event = threading.Event()

    def nextValidId(self, orderId: int) -> None:  # noqa: N802
        self.state.next_valid_order_id = orderId
        self.connected_event.set()

    def error(self, reqId: int, errorCode: int, errorString: str, *args: Any) -> None:  # noqa: N802
        level = "error"
        if errorCode in IBKR_INFO_CODES:
            level = "info"
        elif errorCode in IBKR_WARNING_CODES:
            level = "warning"
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "req_id": reqId,
            "error_code": errorCode,
            "error_string": errorString,
            "level": level,
        }
        with self.state.lock:
            self.state.ibkr_errors.append(payload)
            if len(self.state.ibkr_errors) > 200:
                self.state.ibkr_errors = self.state.ibkr_errors[-200:]

        should_complete_historical = (
            errorCode not in IBKR_INFO_CODES
            and errorCode not in IBKR_WARNING_CODES
            and errorCode not in IBKR_NON_FATAL_HISTORICAL_CODES
        )
        if should_complete_historical and self.state.get_historical_done_event(reqId) is not None:
            self.state.complete_historical_request(reqId, error=f"{errorCode}: {errorString}")

        if self.error_handler is not None:
            self.error_handler(payload)
        _ = args

    def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str) -> None:  # noqa: N802
        _ = (reqId, account, currency)
        try:
            numeric_value = float(value)
        except Exception:
            return
        with self.state.lock:
            self.state.account_values[tag] = numeric_value
        self.account_summary_event.set()

    def position(self, account: str, contract: Any, position: float, avgCost: float) -> None:  # noqa: N802
        _ = account
        symbol = getattr(contract, "symbol", "")
        con_id = getattr(contract, "conId", None)
        if not symbol:
            return

        with self.state.lock:
            previous = self.state.positions.get(symbol)
            self.state.positions[symbol] = PositionSnapshot(
                symbol=symbol,
                con_id=con_id,
                quantity=position,
                market_price=previous.market_price if previous else 0.0,
                market_value=previous.market_value if previous else 0.0,
                average_cost=avgCost,
                unrealized_pnl=previous.unrealized_pnl if previous else 0.0,
                realized_pnl=previous.realized_pnl if previous else 0.0,
            )

    def positionEnd(self) -> None:  # noqa: N802
        self.positions_ready_event.set()

    def updatePortfolio(  # noqa: N802
        self,
        contract: Any,
        position: float,
        marketPrice: float,
        marketValue: float,
        averageCost: float,
        unrealizedPNL: float,
        realizedPNL: float,
        accountName: str,
    ) -> None:
        _ = accountName
        symbol = getattr(contract, "symbol", "")
        con_id = getattr(contract, "conId", None)
        if not symbol:
            return
        with self.state.lock:
            self.state.positions[symbol] = PositionSnapshot(
                symbol=symbol,
                con_id=con_id,
                quantity=position,
                market_price=marketPrice,
                market_value=marketValue,
                average_cost=averageCost,
                unrealized_pnl=unrealizedPNL,
                realized_pnl=realizedPNL,
            )

    def tickPrice(self, reqId: int, tickType: int, price: float, attrib: Any) -> None:  # noqa: N802
        _ = attrib
        with self.state.lock:
            slot = self.state.ticker_values.setdefault(reqId, {})
            if tickType == LAST_PRICE_TICK:
                slot["last"] = price
            elif tickType == PREV_CLOSE_TICK:
                slot["prev_close"] = price
        self.market_data_event.set()

    def tickSize(self, reqId: int, tickType: int, size: float) -> None:  # noqa: N802
        with self.state.lock:
            slot = self.state.ticker_values.setdefault(reqId, {})
            if tickType == VOLUME_TICK:
                slot["volume"] = float(size)
        self.market_data_event.set()

    def pnl(self, reqId: int, dailyPnL: float, unrealizedPnL: float, realizedPnL: float) -> None:  # noqa: N802
        _ = reqId
        with self.state.lock:
            self.state.account_values["DailyPnL"] = dailyPnL
            self.state.account_values["UnrealizedPnL"] = unrealizedPnL
            self.state.account_values["RealizedPnL"] = realizedPnL

    def scannerData(  # noqa: N802
        self,
        reqId: int,
        rank: int,
        contractDetails: Any,
        distance: str,
        benchmark: str,
        projection: str,
        legsStr: str,
    ) -> None:
        _ = (reqId, rank, distance, benchmark, projection, legsStr)
        contract = getattr(contractDetails, "contract", None)
        if contract is None:
            return
        symbol = getattr(contract, "symbol", "")
        con_id = getattr(contract, "conId", 0)
        if not symbol:
            return
        with self.state.lock:
            self.state.scanner_symbols[symbol] = con_id

    def historicalData(self, reqId: int, bar: Any) -> None:  # noqa: N802
        self.state.append_historical_bar(
            reqId,
            {
                "date": getattr(bar, "date", None),
                "open": getattr(bar, "open", 0.0),
                "high": getattr(bar, "high", 0.0),
                "low": getattr(bar, "low", 0.0),
                "close": getattr(bar, "close", 0.0),
                "volume": getattr(bar, "volume", 0.0),
                "wap": getattr(bar, "wap", 0.0),
                "bar_count": getattr(bar, "barCount", 0),
            },
        )

    def historicalDataEnd(self, reqId: int, start: str, end: str) -> None:  # noqa: N802
        _ = (start, end)
        self.state.complete_historical_request(reqId)

    def openOrder(self, orderId: int, contract: Any, order: Any, orderState: Any) -> None:  # noqa: N802
        with self.state.lock:
            self.state.order_events.append(
                {
                    "event": "openOrder",
                    "order_id": orderId,
                    "symbol": getattr(contract, "symbol", ""),
                    "status": getattr(orderState, "status", ""),
                    "action": getattr(order, "action", ""),
                    "order_type": getattr(order, "orderType", ""),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            if len(self.state.order_events) > 1000:
                self.state.order_events = self.state.order_events[-1000:]

    def orderStatus(  # noqa: N802
        self,
        orderId: int,
        status: str,
        filled: float,
        remaining: float,
        avgFillPrice: float,
        permId: int,
        parentId: int,
        lastFillPrice: float,
        clientId: int,
        whyHeld: str,
        mktCapPrice: float,
    ) -> None:
        _ = (permId, parentId, clientId, whyHeld, mktCapPrice)
        with self.state.lock:
            self.state.order_events.append(
                {
                    "event": "orderStatus",
                    "order_id": orderId,
                    "status": status,
                    "filled": filled,
                    "remaining": remaining,
                    "avg_fill_price": avgFillPrice,
                    "last_fill_price": lastFillPrice,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            if len(self.state.order_events) > 1000:
                self.state.order_events = self.state.order_events[-1000:]

    def execDetails(self, reqId: int, contract: Any, execution: Any) -> None:  # noqa: N802
        _ = reqId
        with self.state.lock:
            self.state.order_events.append(
                {
                    "event": "execDetails",
                    "order_id": getattr(execution, "orderId", None),
                    "symbol": getattr(contract, "symbol", ""),
                    "side": getattr(execution, "side", ""),
                    "shares": getattr(execution, "shares", 0.0),
                    "price": getattr(execution, "price", 0.0),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            if len(self.state.order_events) > 1000:
                self.state.order_events = self.state.order_events[-1000:]
