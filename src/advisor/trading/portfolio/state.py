from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Dict

from advisor.trading.types import PositionState, Side


def load_positions(path: str) -> Dict[str, PositionState]:
    p = Path(path)
    if not p.exists():
        return {}
    payload = json.loads(p.read_text(encoding="utf-8"))
    result: Dict[str, PositionState] = {}
    for item in payload.get("positions", []):
        result[item["symbol"]] = PositionState(
            symbol=item["symbol"],
            side=Side(item["side"]),
            contracts=int(item["contracts"]),
            entry_price=float(item["entry_price"]),
            stop_price=float(item["stop_price"]),
            target_price=float(item["target_price"]) if item.get("target_price") is not None else None,
            opened_at=datetime.fromisoformat(item["opened_at"]),
            strategy_name=item["strategy_name"],
            reason_codes=list(item.get("reason_codes", [])),
            mae=float(item.get("mae", 0.0)),
            mfe=float(item.get("mfe", 0.0)),
        )
    return result


def save_positions(path: str, positions: Dict[str, PositionState]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for pos in positions.values():
        raw = asdict(pos)
        raw["side"] = pos.side.value
        raw["opened_at"] = pos.opened_at.isoformat()
        rows.append(raw)
    p.write_text(json.dumps({"positions": rows}, indent=2), encoding="utf-8")
