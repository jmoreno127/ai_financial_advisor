from __future__ import annotations

from typing import Optional

try:
    from ibapi.scanner import ScannerSubscription
except Exception:  # pragma: no cover - optional dependency import guard
    ScannerSubscription = None  # type: ignore[assignment]


def build_top_movers_subscription(max_results: int = 20):
    if ScannerSubscription is None:
        return None

    subscription = ScannerSubscription()
    subscription.instrument = "STK"
    subscription.locationCode = "STK.US.MAJOR"
    subscription.scanCode = "TOP_PERC_GAIN"
    subscription.numberOfRows = max_results
    return subscription


def build_most_active_subscription(max_results: int = 20):
    if ScannerSubscription is None:
        return None

    subscription = ScannerSubscription()
    subscription.instrument = "STK"
    subscription.locationCode = "STK.US.MAJOR"
    subscription.scanCode = "HOT_BY_VOLUME"
    subscription.numberOfRows = max_results
    return subscription
