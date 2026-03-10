from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Dict, List

import pandas as pd

SESSION_TEMPLATES: Dict[str, Dict[str, time]] = {
    "CME_EQUITY": {
        "rth_start": time(9, 30),
        "rth_end": time(16, 0),
    },
    "COMEX_METALS": {
        "rth_start": time(8, 20),
        "rth_end": time(13, 30),
    },
}

SYMBOL_SESSION_CLASS = {
    "MES": "CME_EQUITY",
    "MNQ": "CME_EQUITY",
    "M2K": "CME_EQUITY",
    "MYM": "CME_EQUITY",
    "MGC": "COMEX_METALS",
    "SI": "COMEX_METALS",
}


def load_bars_from_path(path: str | Path) -> pd.DataFrame:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(file_path)
    elif suffix in {".parquet", ".pq"}:
        df = pd.read_parquet(file_path)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")
    return normalize_bars(df)


def normalize_bars(df: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close", "volume", "symbol"}
    missing = required.difference(set(df.columns))
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
    out = out.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    out["symbol"] = out["symbol"].astype(str)
    return out


def add_common_features(df: pd.DataFrame, atr_window: int = 14, volume_window: int = 20) -> pd.DataFrame:
    out = df.copy()
    out["session_kind"] = out.apply(_session_kind_for_row, axis=1)
    out["trade_date"] = out["timestamp"].dt.strftime("%Y-%m-%d")

    prev_close = out.groupby("symbol")["close"].shift(1)
    high_low = out["high"] - out["low"]
    high_prev = (out["high"] - prev_close).abs()
    low_prev = (out["low"] - prev_close).abs()
    out["tr"] = pd.concat([high_low, high_prev, low_prev], axis=1).max(axis=1)
    out["atr"] = out.groupby("symbol")["tr"].transform(lambda s: s.rolling(atr_window, min_periods=1).mean())

    out["volume_avg"] = out.groupby("symbol")["volume"].transform(
        lambda s: s.rolling(volume_window, min_periods=1).mean()
    )

    typical = (out["high"] + out["low"] + out["close"]) / 3.0
    out["tpv"] = typical * out["volume"].fillna(0.0)
    out["cum_tpv"] = out.groupby(["symbol", "trade_date"])["tpv"].cumsum()
    out["cum_vol"] = out.groupby(["symbol", "trade_date"])["volume"].cumsum().replace(0, pd.NA)
    out["vwap"] = (out["cum_tpv"] / out["cum_vol"]).fillna(out["close"])

    out["prior_day_high"] = out.groupby("symbol")["high"].transform(lambda s: s.shift(1).rolling(78, min_periods=1).max())
    out["prior_day_low"] = out.groupby("symbol")["low"].transform(lambda s: s.shift(1).rolling(78, min_periods=1).min())

    return out


def opening_range(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    out = df.copy()
    out["timestamp_et"] = out["timestamp"].dt.tz_convert("America/New_York")
    session_start = out.apply(_rth_start_for_row, axis=1)
    out["or_window_end"] = session_start + pd.to_timedelta(minutes, unit="m")
    out["in_or_window"] = (out["timestamp_et"] >= session_start) & (out["timestamp_et"] < out["or_window_end"])

    or_rows = out[out["in_or_window"]]
    if or_rows.empty:
        out["or_high"] = pd.NA
        out["or_low"] = pd.NA
        out["or_ready"] = False
        return out.drop(columns=["timestamp_et", "in_or_window", "or_window_end"])

    grouped = or_rows.groupby(["symbol", "trade_date"]).agg(or_high=("high", "max"), or_low=("low", "min"))
    grouped["or_ready"] = True
    out = out.merge(grouped.reset_index(), on=["symbol", "trade_date"], how="left")
    out["or_ready"] = out["or_ready"].fillna(False)
    return out.drop(columns=["timestamp_et", "in_or_window", "or_window_end"])


def filter_session_scope(df: pd.DataFrame, allowed: List[str]) -> pd.DataFrame:
    allow = {item.upper().strip() for item in allowed}
    return df[df["session_kind"].isin(allow)].copy()


def _session_kind_for_row(row: pd.Series) -> str:
    ts = pd.Timestamp(row["timestamp"]).tz_convert("America/New_York")
    symbol_root = str(row["symbol"]).split("-")[0].split(":")[0].upper()
    session_class = SYMBOL_SESSION_CLASS.get(symbol_root, "CME_EQUITY")
    template = SESSION_TEMPLATES[session_class]
    rth_start = template["rth_start"]
    rth_end = template["rth_end"]
    tod = ts.time()
    if rth_start <= tod < rth_end:
        return "RTH"
    return "ETH"


def _rth_start_for_row(row: pd.Series) -> pd.Timestamp:
    ts = pd.Timestamp(row["timestamp"]).tz_convert("America/New_York")
    symbol_root = str(row["symbol"]).split("-")[0].split(":")[0].upper()
    session_class = SYMBOL_SESSION_CLASS.get(symbol_root, "CME_EQUITY")
    template = SESSION_TEMPLATES[session_class]
    return pd.Timestamp(f"{ts.date().isoformat()} {template['rth_start'].strftime('%H:%M:%S')}").tz_localize(
        "America/New_York"
    )
