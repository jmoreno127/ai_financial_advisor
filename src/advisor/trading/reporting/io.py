from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from advisor.trading.types import FillEvent, ValidationResult


def write_trade_outputs(base_dir: str, name: str, trades: List[FillEvent], equity_curve: List[float]) -> None:
    out_dir = Path(base_dir) / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)

    trades_rows = []
    for trade in trades:
        row = asdict(trade)
        row["side"] = trade.side.value
        row["entry_ts"] = trade.entry_ts.isoformat()
        row["exit_ts"] = trade.exit_ts.isoformat()
        row["exit_reason"] = trade.exit_reason.value
        trades_rows.append(row)

    pd.DataFrame(trades_rows).to_csv(out_dir / f"{name}_trades.csv", index=False)
    pd.DataFrame({"equity": equity_curve}).to_csv(out_dir / f"{name}_equity.csv", index=False)


def write_validation_output(base_dir: str, result: ValidationResult) -> None:
    out_dir = Path(base_dir) / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = asdict(result)
    (out_dir / "latest_validation.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def load_validation_output(base_dir: str) -> Dict[str, Any] | None:
    path = Path(base_dir) / "reports" / "latest_validation.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
