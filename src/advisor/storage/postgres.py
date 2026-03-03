from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from advisor.models import DecisionRecord, InstrumentSnapshot, PortfolioSnapshot, TriggerEvent


class PostgresStore:
    def __init__(self, dsn: str):
        self.engine: Engine = create_engine(dsn, future=True, pool_pre_ping=True)
        self._schema_initialized = False

    def init_schema(self) -> None:
        if self._schema_initialized:
            return

        schema_path = Path(__file__).with_name("schema.sql")
        sql = schema_path.read_text(encoding="utf-8")
        with self.engine.begin() as conn:
            for statement in [chunk.strip() for chunk in sql.split(";") if chunk.strip()]:
                conn.execute(text(statement))
        self._schema_initialized = True

    def write_cycle(
        self,
        portfolio: PortfolioSnapshot,
        instruments: List[InstrumentSnapshot],
        triggers: List[TriggerEvent],
        decision: DecisionRecord,
    ) -> None:
        self.init_schema()

        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO portfolio_snapshots (cycle_ts, account_id, payload)
                    VALUES (:cycle_ts, :account_id, CAST(:payload AS JSONB))
                    ON CONFLICT (cycle_ts, account_id)
                    DO UPDATE SET payload = EXCLUDED.payload
                    """
                ),
                {
                    "cycle_ts": portfolio.cycle_ts,
                    "account_id": portfolio.account_id,
                    "payload": json.dumps(portfolio.model_dump(mode="json"), default=str),
                },
            )

            for position in portfolio.positions:
                conn.execute(
                    text(
                        """
                        INSERT INTO position_snapshots (cycle_ts, account_id, symbol, payload)
                        VALUES (:cycle_ts, :account_id, :symbol, CAST(:payload AS JSONB))
                        ON CONFLICT (cycle_ts, account_id, symbol)
                        DO UPDATE SET payload = EXCLUDED.payload
                        """
                    ),
                    {
                        "cycle_ts": portfolio.cycle_ts,
                        "account_id": portfolio.account_id,
                        "symbol": position.symbol,
                        "payload": json.dumps(position.model_dump(mode="json"), default=str),
                    },
                )

            for instrument in instruments:
                conn.execute(
                    text(
                        """
                        INSERT INTO instrument_snapshots (cycle_ts, symbol, source, payload)
                        VALUES (:cycle_ts, :symbol, :source, CAST(:payload AS JSONB))
                        ON CONFLICT (cycle_ts, symbol, source)
                        DO UPDATE SET payload = EXCLUDED.payload
                        """
                    ),
                    {
                        "cycle_ts": portfolio.cycle_ts,
                        "symbol": instrument.symbol,
                        "source": instrument.source,
                        "payload": json.dumps(instrument.model_dump(mode="json"), default=str),
                    },
                )

            for trigger in triggers:
                conn.execute(
                    text(
                        """
                        INSERT INTO trigger_events (cycle_ts, account_id, name, symbol, payload)
                        VALUES (:cycle_ts, :account_id, :name, :symbol, CAST(:payload AS JSONB))
                        """
                    ),
                    {
                        "cycle_ts": portfolio.cycle_ts,
                        "account_id": portfolio.account_id,
                        "name": trigger.name,
                        "symbol": trigger.symbol,
                        "payload": json.dumps(trigger.model_dump(mode="json"), default=str),
                    },
                )

            conn.execute(
                text(
                    """
                    INSERT INTO ai_decisions (
                        cycle_ts,
                        account_id,
                        model_used,
                        deep_analysis,
                        request_payload,
                        recommendation_payload,
                        raw_response
                    ) VALUES (
                        :cycle_ts,
                        :account_id,
                        :model_used,
                        :deep_analysis,
                        CAST(:request_payload AS JSONB),
                        CAST(:recommendation_payload AS JSONB),
                        :raw_response
                    )
                    ON CONFLICT (cycle_ts, account_id)
                    DO UPDATE SET
                        model_used = EXCLUDED.model_used,
                        deep_analysis = EXCLUDED.deep_analysis,
                        request_payload = EXCLUDED.request_payload,
                        recommendation_payload = EXCLUDED.recommendation_payload,
                        raw_response = EXCLUDED.raw_response
                    """
                ),
                {
                    "cycle_ts": decision.cycle_ts,
                    "account_id": decision.account_id,
                    "model_used": decision.model_used,
                    "deep_analysis": decision.deep_analysis,
                    "request_payload": json.dumps(decision.request_payload, default=str),
                    "recommendation_payload": json.dumps(
                        decision.recommendation.model_dump(mode="json"), default=str
                    ),
                    "raw_response": decision.raw_response,
                },
            )

    def heartbeat(self, service_name: str, status: str, details: dict | None = None) -> None:
        self.init_schema()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO service_heartbeats (service_name, status, details)
                    VALUES (:service_name, :status, CAST(:details AS JSONB))
                    """
                ),
                {
                    "service_name": service_name,
                    "status": status,
                    "details": json.dumps(details or {}),
                },
            )

    def doctor(self) -> None:
        self.init_schema()
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    def latest_decision(self, account_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        self.init_schema()
        sql = """
            SELECT cycle_ts, account_id, model_used, deep_analysis, request_payload, recommendation_payload, raw_response
            FROM ai_decisions
        """
        params: Dict[str, Any] = {}
        if account_id:
            sql += " WHERE account_id = :account_id"
            params["account_id"] = account_id
        sql += " ORDER BY cycle_ts DESC LIMIT 1"

        with self.engine.connect() as conn:
            row = conn.execute(text(sql), params).mappings().first()
            if row is None:
                return None
            return {
                "cycle_ts": str(row["cycle_ts"]),
                "account_id": row["account_id"],
                "model_used": row["model_used"],
                "deep_analysis": row["deep_analysis"],
                "request_payload": _json_like(row["request_payload"]),
                "recommendation_payload": _json_like(row["recommendation_payload"]),
                "raw_response": row["raw_response"],
            }


def _json_like(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value
