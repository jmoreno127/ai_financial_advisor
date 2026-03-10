from __future__ import annotations

from datetime import datetime, timezone

from advisor.trading.portfolio.state import load_positions, save_positions
from advisor.trading.types import PositionState, Side


def test_position_state_save_and_reload(tmp_path) -> None:
    path = tmp_path / "state.json"
    initial = {
        "MES-202606-CME": PositionState(
            symbol="MES-202606-CME",
            side=Side.LONG,
            contracts=2,
            entry_price=100.0,
            stop_price=99.0,
            target_price=102.0,
            opened_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
            strategy_name="orb",
        )
    }

    save_positions(str(path), initial)
    loaded = load_positions(str(path))

    assert "MES-202606-CME" in loaded
    assert loaded["MES-202606-CME"].contracts == 2
