from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import Engine

from advisor.models import DecisionRecord, HistoricalBar, InstrumentSnapshot, PortfolioSnapshot, TriggerEvent


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

    def write_followup_turn(
        self,
        conversation_id: str,
        turn_index: int,
        model_used: str,
        user_question: str,
        assistant_answer: str,
        account_id: Optional[str] = None,
        decision_cycle_ts: Optional[str] = None,
        context_payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.init_schema()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO ai_followup_turns (
                        conversation_id,
                        turn_index,
                        account_id,
                        decision_cycle_ts,
                        model_used,
                        user_question,
                        assistant_answer,
                        context_payload
                    ) VALUES (
                        :conversation_id,
                        :turn_index,
                        :account_id,
                        :decision_cycle_ts,
                        :model_used,
                        :user_question,
                        :assistant_answer,
                        CAST(:context_payload AS JSONB)
                    )
                    ON CONFLICT (conversation_id, turn_index)
                    DO UPDATE SET
                        model_used = EXCLUDED.model_used,
                        user_question = EXCLUDED.user_question,
                        assistant_answer = EXCLUDED.assistant_answer,
                        context_payload = EXCLUDED.context_payload
                    """
                ),
                {
                    "conversation_id": conversation_id,
                    "turn_index": turn_index,
                    "account_id": account_id,
                    "decision_cycle_ts": decision_cycle_ts,
                    "model_used": model_used,
                    "user_question": user_question,
                    "assistant_answer": assistant_answer,
                    "context_payload": json.dumps(context_payload or {}, default=str),
                },
            )

    def instrument_history(
        self,
        symbols: List[str],
        since_ts: datetime,
    ) -> Dict[str, List[Dict[str, Any]]]:
        self.init_schema()
        if not symbols:
            return {}

        stmt = (
            text(
                """
                SELECT cycle_ts, symbol, source, payload
                FROM instrument_snapshots
                WHERE symbol IN :symbols
                  AND cycle_ts >= :since_ts
                ORDER BY symbol ASC, cycle_ts ASC
                """
            )
            .bindparams(bindparam("symbols", expanding=True))
        )

        result: Dict[str, List[Dict[str, Any]]] = {symbol: [] for symbol in symbols}
        with self.engine.connect() as conn:
            rows = conn.execute(stmt, {"symbols": symbols, "since_ts": since_ts}).mappings()
            for row in rows:
                payload = _json_like(row["payload"])
                if not isinstance(payload, dict):
                    payload = {}
                symbol = str(row["symbol"])
                result.setdefault(symbol, []).append(
                    {
                        "cycle_ts": row["cycle_ts"],
                        "symbol": symbol,
                        "source": row["source"],
                        "last_price": payload.get("last_price", 0.0),
                        "volume": payload.get("volume", 0.0),
                        "pct_change": payload.get("pct_change", 0.0),
                    }
                )
        return result

    def upsert_historical_bars(self, bars: List[HistoricalBar]) -> None:
        self.init_schema()
        if not bars:
            return

        payload = [bar.model_dump(mode="json") for bar in bars]
        stmt = text(
            """
            INSERT INTO instrument_historical_bars (
                instrument_key,
                bar_ts,
                open,
                high,
                low,
                close,
                volume,
                wap,
                bar_count,
                bar_size,
                what_to_show,
                use_rth,
                source,
                fetched_at
            ) VALUES (
                :instrument_key,
                :bar_ts,
                :open,
                :high,
                :low,
                :close,
                :volume,
                :wap,
                :bar_count,
                :bar_size,
                :what_to_show,
                :use_rth,
                :source,
                :fetched_at
            )
            ON CONFLICT (instrument_key, bar_ts, bar_size, what_to_show, use_rth)
            DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume,
                wap = EXCLUDED.wap,
                bar_count = EXCLUDED.bar_count,
                source = EXCLUDED.source,
                fetched_at = EXCLUDED.fetched_at
            """
        )
        with self.engine.begin() as conn:
            conn.execute(stmt, payload)

    def historical_bars(
        self,
        symbols: List[str],
        since_ts: datetime,
        bar_size: str,
        what_to_show: str,
        use_rth: bool,
    ) -> Dict[str, List[Dict[str, Any]]]:
        self.init_schema()
        if not symbols:
            return {}

        stmt = (
            text(
                """
                SELECT
                    instrument_key,
                    bar_ts,
                    open,
                    high,
                    low,
                    close,
                    volume,
                    wap,
                    bar_count,
                    source,
                    fetched_at
                FROM instrument_historical_bars
                WHERE instrument_key IN :symbols
                  AND bar_ts >= :since_ts
                  AND bar_size = :bar_size
                  AND what_to_show = :what_to_show
                  AND use_rth = :use_rth
                ORDER BY instrument_key ASC, bar_ts ASC
                """
            )
            .bindparams(bindparam("symbols", expanding=True))
        )

        result: Dict[str, List[Dict[str, Any]]] = {symbol: [] for symbol in symbols}
        with self.engine.connect() as conn:
            rows = conn.execute(
                stmt,
                {
                    "symbols": symbols,
                    "since_ts": since_ts,
                    "bar_size": bar_size,
                    "what_to_show": what_to_show,
                    "use_rth": use_rth,
                },
            ).mappings()
            for row in rows:
                symbol = str(row["instrument_key"])
                result.setdefault(symbol, []).append(
                    {
                        "cycle_ts": row["bar_ts"],
                        "open": float(row["open"] or 0.0),
                        "high": float(row["high"] or 0.0),
                        "low": float(row["low"] or 0.0),
                        "last_price": float(row["close"] or 0.0),
                        "volume": float(row["volume"] or 0.0),
                        "wap": float(row["wap"] or 0.0),
                        "bar_count": int(row["bar_count"] or 0),
                        "source": row["source"],
                        "fetched_at": row["fetched_at"],
                    }
                )
        return result

    def prune_historical_bars(self, retention_days: int) -> int:
        self.init_schema()
        retention_days = max(1, int(retention_days))
        cutoff_ts = datetime.now(timezone.utc) - timedelta(days=retention_days)
        stmt = text(
            """
            DELETE FROM instrument_historical_bars
            WHERE bar_ts < :cutoff_ts
            """
        )
        with self.engine.begin() as conn:
            result = conn.execute(stmt, {"cutoff_ts": cutoff_ts})
            return int(result.rowcount or 0)

    def set_trading_kill_switch(self, enabled: bool) -> None:
        self.init_schema()
        payload = json.dumps({"enabled": bool(enabled)}, default=str)
        payload_sql = "CAST(:payload AS JSONB)" if self.engine.dialect.name.startswith("postgresql") else ":payload"
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    INSERT INTO trading_controls (control_key, payload, updated_at)
                    VALUES (:control_key, {payload_sql}, CURRENT_TIMESTAMP)
                    ON CONFLICT (control_key)
                    DO UPDATE SET payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at
                    """
                ),
                {"control_key": "kill_switch", "payload": payload},
            )

    def get_trading_kill_switch(self) -> bool:
        self.init_schema()
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT payload
                    FROM trading_controls
                    WHERE control_key = :control_key
                    """
                ),
                {"control_key": "kill_switch"},
            ).mappings().first()
            if row is None:
                return False
            payload = _json_like(row["payload"])
            if isinstance(payload, dict):
                return bool(payload.get("enabled"))
            return False

    def write_trading_event(
        self,
        event_type: str,
        symbol: str | None,
        strategy: str | None,
        payload: Dict[str, Any] | None = None,
    ) -> None:
        self.init_schema()
        payload_sql = "CAST(:payload AS JSONB)" if self.engine.dialect.name.startswith("postgresql") else ":payload"
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    INSERT INTO trading_events (event_type, symbol, strategy, payload)
                    VALUES (:event_type, :symbol, :strategy, {payload_sql})
                    """
                ),
                {
                    "event_type": event_type,
                    "symbol": symbol,
                    "strategy": strategy,
                    "payload": json.dumps(payload or {}, default=str),
                },
            )


def _json_like(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value
