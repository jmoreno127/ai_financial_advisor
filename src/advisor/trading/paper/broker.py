from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from advisor.ibkr.client import IBKRClient
from advisor.trading.types import OrderIntent, Side


class BrokerAdapter(Protocol):
    def place_entry(self, order: OrderIntent) -> str:
        ...


@dataclass(slots=True)
class IBKRPaperBrokerAdapter:
    ibkr: IBKRClient

    def place_entry(self, order: OrderIntent) -> str:
        action = "BUY" if order.side == Side.LONG else "SELL"
        order_id = self.ibkr.place_order(
            instrument_entry=order.symbol,
            action=action,
            quantity=order.contracts,
            order_type="MKT",
            tif="DAY",
            transmit=True,
        )
        return str(order_id)


@dataclass(slots=True)
class RecordingBrokerAdapter:
    placed: list[OrderIntent]

    def place_entry(self, order: OrderIntent) -> str:
        self.placed.append(order)
        return f"mock-{len(self.placed)}"
