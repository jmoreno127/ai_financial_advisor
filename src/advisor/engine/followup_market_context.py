from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev
from typing import Any, Dict, Iterable, List, Sequence, Tuple

WINDOW_SPECS: Tuple[Tuple[str, timedelta], ...] = (
    ("1w", timedelta(days=7)),
    ("3d", timedelta(days=3)),
    ("5h", timedelta(hours=5)),
)

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9:_\-.]+")


def canonical_instrument_key(text: str) -> str | None:
    token = text.strip().strip(",;.!?()[]{}<>\"'").upper()
    if not token:
        return None

    dash_parts = token.split("-")
    if len(dash_parts) == 3 and dash_parts[1].isdigit() and len(dash_parts[1]) in (6, 8):
        return f"{dash_parts[0]}-{dash_parts[1]}-{dash_parts[2]}"

    parts = [part.strip() for part in token.split(":") if part.strip()]
    if not parts:
        return None

    head = parts[0]
    if head == "FUT":
        if len(parts) < 4:
            return None
        symbol = parts[1]
        expiry = parts[2]
        exchange = parts[3]
        if not expiry.isdigit() or len(expiry) not in (6, 8):
            return None
        return f"{symbol}-{expiry}-{exchange}"

    if head == "STK":
        if len(parts) < 2:
            return None
        return parts[1]

    if len(parts) >= 3 and parts[1].isdigit() and len(parts[1]) in (6, 8):
        return f"{parts[0]}-{parts[1]}-{parts[2]}"

    symbol = parts[0]
    if re.fullmatch(r"[A-Z][A-Z0-9._]{0,15}", symbol):
        return symbol
    return None


def extract_requested_instruments(question: str, known_symbols: Iterable[str]) -> List[str]:
    known_map: Dict[str, str] = {}
    for symbol in known_symbols:
        canonical = canonical_instrument_key(symbol)
        if canonical:
            known_map[canonical] = canonical

    found: List[str] = []
    seen: set[str] = set()
    for raw_token in _TOKEN_PATTERN.findall(question):
        canonical = canonical_instrument_key(raw_token)
        if canonical is None:
            continue

        is_futures_notation = ":" in raw_token or (
            "-" in raw_token and len(canonical.split("-")) == 3 and canonical.split("-")[1].isdigit()
        )
        if not is_futures_notation and canonical not in known_map:
            continue

        if canonical in seen:
            continue
        seen.add(canonical)
        found.append(canonical)

    return found


def build_followup_market_context(
    question: str,
    known_symbols: Iterable[str],
    history_by_symbol: Dict[str, Sequence[Dict[str, Any]]],
    now: datetime | None = None,
    snapshot_fallback_by_symbol: Dict[str, Sequence[Dict[str, Any]]] | None = None,
    fetch_failures: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    requested_symbols = extract_requested_instruments(question, known_symbols)
    metrics_by_symbol: Dict[str, Any] = {}
    source_summary = {
        "ibkr_historical_cache": 0,
        "snapshot_fallback": 0,
        "no_data": 0,
        "fresh": 0,
        "fallback": 0,
        "missing": 0,
    }

    fallback_map = snapshot_fallback_by_symbol or {}
    failure_map = fetch_failures or {}

    for symbol in requested_symbols:
        primary_points = sorted(history_by_symbol.get(symbol, []), key=_sort_cycle_ts)
        fallback_points = sorted(fallback_map.get(symbol, []), key=_sort_cycle_ts)
        failure_reason = failure_map.get(symbol)

        if primary_points:
            summary = _summarize_symbol(primary_points, now_utc, data_source="ibkr_historical_cache", data_quality="fresh")
        elif fallback_points:
            summary = _summarize_symbol(
                fallback_points,
                now_utc,
                data_source="snapshot_fallback",
                data_quality="fallback",
                fallback_reason=failure_reason or "historical cache empty",
            )
        else:
            summary = {
                "status": "no_data",
                "latest": None,
                "windows": {},
                "data_source": "no_data",
                "data_quality": "missing",
                "fallback_reason": failure_reason or "No historical or fallback snapshot data",
            }

        source_summary[summary["data_source"]] = source_summary.get(summary["data_source"], 0) + 1
        source_summary[summary["data_quality"]] = source_summary.get(summary["data_quality"], 0) + 1
        metrics_by_symbol[symbol] = summary

    return {
        "generated_at_utc": now_utc.isoformat(),
        "requested_symbols": requested_symbols,
        "window_definitions": {name: str(duration) for name, duration in WINDOW_SPECS},
        "source_summary": source_summary,
        "metrics_by_symbol": metrics_by_symbol,
    }


def _summarize_symbol(
    points: Sequence[Dict[str, Any]],
    now_utc: datetime,
    *,
    data_source: str,
    data_quality: str,
    fallback_reason: str | None = None,
) -> Dict[str, Any]:
    if not points:
        return {
            "status": "no_data",
            "latest": None,
            "windows": {},
            "data_source": "no_data",
            "data_quality": "missing",
            "fallback_reason": fallback_reason or "No data points",
        }

    normalized: List[Dict[str, Any]] = []
    for item in points:
        ts = item.get("cycle_ts")
        if not isinstance(ts, datetime):
            continue
        ts_utc = ts.astimezone(timezone.utc)
        close_price = _as_float(item.get("last_price"))
        high_price = _as_float(item.get("high")) or close_price
        low_price = _as_float(item.get("low")) or close_price
        open_price = _as_float(item.get("open")) or close_price
        normalized.append(
            {
                "cycle_ts": ts_utc,
                "open": open_price,
                "high": max(high_price, close_price, open_price),
                "low": min(low_price, close_price, open_price),
                "last_price": close_price,
                "volume": max(0.0, _as_float(item.get("volume"))),
                "pct_change": _as_float(item.get("pct_change")),
                "source": item.get("source", data_source),
            }
        )

    if not normalized:
        return {
            "status": "no_data",
            "latest": None,
            "windows": {},
            "data_source": "no_data",
            "data_quality": "missing",
            "fallback_reason": fallback_reason or "No valid time-series points",
        }

    latest_point = normalized[-1]
    windows: Dict[str, Any] = {}
    for name, duration in WINDOW_SPECS:
        cutoff = now_utc - duration
        window_points = [item for item in normalized if item["cycle_ts"] >= cutoff]
        windows[name] = _summarize_window(window_points)

    return {
        "status": "ok",
        "data_source": data_source,
        "data_quality": data_quality,
        "fallback_reason": fallback_reason,
        "latest": {
            "cycle_ts": latest_point["cycle_ts"].isoformat(),
            "last_price": latest_point["last_price"],
            "open": latest_point["open"],
            "high": latest_point["high"],
            "low": latest_point["low"],
            "volume": latest_point["volume"],
            "pct_change": latest_point["pct_change"],
            "source": latest_point["source"],
        },
        "coverage": {
            "start_utc": normalized[0]["cycle_ts"].isoformat(),
            "end_utc": normalized[-1]["cycle_ts"].isoformat(),
            "sample_count": len(normalized),
        },
        "windows": windows,
        "recent_points_5h": _recent_points_for_window(normalized, now_utc - timedelta(hours=5), limit=12),
    }


def _summarize_window(points: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    if not points:
        return {"status": "no_data", "sample_count": 0}

    closes = [item["last_price"] for item in points if item["last_price"] > 0]
    if not closes:
        return {"status": "no_price_data", "sample_count": len(points)}

    volumes = [max(0.0, item["volume"]) for item in points]
    start_price = closes[0]
    end_price = closes[-1]
    high_price = max(item["high"] for item in points if item["high"] > 0) if points else max(closes)
    low_price = min(item["low"] for item in points if item["low"] > 0) if points else min(closes)

    total_volume = sum(volumes)
    weighted_sum = 0.0
    weighted_volume = 0.0
    for item in points:
        if item["last_price"] > 0 and item["volume"] > 0:
            weighted_sum += item["last_price"] * item["volume"]
            weighted_volume += item["volume"]
    vwap = weighted_sum / weighted_volume if weighted_volume > 0 else mean(closes)

    returns = _compute_step_returns(closes)
    realized_vol_pct = pstdev(returns) if returns else 0.0
    up_move_ratio = sum(1 for value in returns if value > 0) / len(returns) if returns else 0.0

    return_pct = _pct_diff(end_price, start_price)
    range_pct = _pct_diff(high_price, low_price) if low_price > 0 else 0.0
    price_vs_vwap_pct = _pct_diff(end_price, vwap) if vwap > 0 else 0.0

    first_ts = points[0]["cycle_ts"]
    last_ts = points[-1]["cycle_ts"]
    span_minutes = max(0.0, (last_ts - first_ts).total_seconds() / 60.0)

    return {
        "status": "ok",
        "sample_count": len(points),
        "start_utc": first_ts.isoformat(),
        "end_utc": last_ts.isoformat(),
        "span_minutes": span_minutes,
        "start_price": start_price,
        "end_price": end_price,
        "high_price": high_price,
        "low_price": low_price,
        "mean_price": mean(closes),
        "vwap": vwap,
        "total_volume": total_volume,
        "return_pct": return_pct,
        "range_pct": range_pct,
        "price_vs_vwap_pct": price_vs_vwap_pct,
        "realized_volatility_pct_per_step": realized_vol_pct,
        "max_drawdown_pct": _max_drawdown_pct(closes),
        "up_move_ratio": up_move_ratio,
    }


def _recent_points_for_window(
    points: Sequence[Dict[str, Any]],
    cutoff: datetime,
    limit: int = 12,
) -> List[Dict[str, Any]]:
    recent = [item for item in points if item["cycle_ts"] >= cutoff]
    recent = recent[-limit:]
    return [
        {
            "cycle_ts": item["cycle_ts"].isoformat(),
            "open": item["open"],
            "high": item["high"],
            "low": item["low"],
            "last_price": item["last_price"],
            "volume": item["volume"],
            "pct_change": item["pct_change"],
        }
        for item in recent
    ]


def _compute_step_returns(prices: Sequence[float]) -> List[float]:
    results: List[float] = []
    for idx in range(1, len(prices)):
        prev_price = prices[idx - 1]
        curr_price = prices[idx]
        if prev_price <= 0:
            continue
        results.append(((curr_price - prev_price) / prev_price) * 100.0)
    return results


def _max_drawdown_pct(prices: Sequence[float]) -> float:
    peak = prices[0]
    max_drawdown = 0.0
    for price in prices:
        if price > peak:
            peak = price
            continue
        if peak <= 0:
            continue
        drawdown = ((peak - price) / peak) * 100.0
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    return max_drawdown


def _pct_diff(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return ((a - b) / b) * 100.0


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _sort_cycle_ts(item: Dict[str, Any]) -> datetime:
    value = item.get("cycle_ts")
    if isinstance(value, datetime):
        return value
    return datetime.min.replace(tzinfo=timezone.utc)
