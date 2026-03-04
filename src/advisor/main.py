from __future__ import annotations

import argparse
import socket
from datetime import datetime, timedelta, timezone
from typing import Dict, List
from uuid import uuid4

from apscheduler.schedulers.blocking import BlockingScheduler

from advisor.ai.langchain_flow import AIAnalyzer
from advisor.config import AppConfig
from advisor.engine.followup_market_context import (
    build_followup_market_context,
    canonical_instrument_key,
    extract_requested_instruments,
)
from advisor.engine.metrics import RollingWindowState, compute_risk_metrics
from advisor.engine.risk_policy import apply_balanced_swing_policy
from advisor.engine.triggers import evaluate_triggers, should_run_deep_analysis
from advisor.ibkr.client import IBKRClient
from advisor.models import AnalysisRequest, DecisionRecord
from advisor.output.logger import StructuredLogger
from advisor.storage.postgres import PostgresStore


class AdvisorService:
    def __init__(self, config: AppConfig):
        self.config = config
        self.logger = StructuredLogger(config.json_log_path)
        self.store = PostgresStore(config.postgres_dsn)
        self.ai = AIAnalyzer(config)
        self.ibkr = IBKRClient(config, error_handler=self._on_ibkr_error)
        self.rolling = RollingWindowState()

    def start(self) -> None:
        self.ibkr.start()
        self.store.heartbeat("advisor", "started", {"interval_seconds": self.config.run_interval_seconds})
        self.logger.info("Advisor service started")

    def stop(self) -> None:
        self.ibkr.stop()
        self.store.heartbeat("advisor", "stopped")
        self.logger.info("Advisor service stopped")

    def collect_cycle(self) -> DecisionRecord:
        cycle_ts = datetime.now(timezone.utc)

        if not self.ibkr.reconnect_if_needed():
            raise RuntimeError("IBKR is disconnected and reconnect attempts failed")

        self.ibkr.request_scanner_refresh()
        symbols = set(self.config.watchlist)
        symbols.update(self.ibkr.scanner_symbols())
        self.ibkr.ensure_market_data_subscriptions(symbols)
        ready = self.ibkr.wait_for_initial_data(
            timeout_seconds=60,
            progress_interval_seconds=10,
            progress_callback=self._on_connectivity_progress,
        )
        if not ready:
            self.logger.error("Initial IBKR data wait timed out; continuing with partial snapshot")

        portfolio, instruments = self.ibkr.collect_snapshot(cycle_ts)
        self.rolling.update(portfolio, instruments)

        risk = compute_risk_metrics(
            portfolio=portfolio,
            max_margin_utilization=self.config.max_margin_utilization,
            max_single_name_exposure=self.config.max_single_name_exposure,
            max_gross_leverage=self.config.max_gross_leverage,
            max_drawdown_from_day_high=self.config.max_drawdown_from_day_high,
        )

        triggers = evaluate_triggers(self.config, portfolio, instruments, self.rolling)
        deep_analysis = should_run_deep_analysis(triggers, risk)

        analysis_request = AnalysisRequest(
            cycle_ts=cycle_ts,
            portfolio=portfolio,
            risk_metrics=risk,
            triggers=triggers,
            key_instruments=instruments,
        )

        recommendation, model_used, raw_response = self.ai.analyze(analysis_request, deep=deep_analysis)
        recommendation = apply_balanced_swing_policy(recommendation, risk)

        decision = DecisionRecord(
            cycle_ts=cycle_ts,
            account_id=portfolio.account_id,
            model_used=model_used,
            deep_analysis=deep_analysis,
            request_payload=analysis_request.model_dump(mode="json"),
            recommendation=recommendation,
            raw_response=raw_response,
        )

        self.store.write_cycle(portfolio, instruments, triggers, decision)
        self.store.heartbeat(
            "advisor",
            "ok",
            {
                "deep_analysis": deep_analysis,
                "trigger_count": len(triggers),
                "decision": recommendation.decision,
                "action_type": recommendation.action_type,
            },
        )

        self.logger.info(
            "Cycle completed",
            cycle_ts=cycle_ts.isoformat(),
            account_id=portfolio.account_id,
            trigger_count=len(triggers),
            deep_analysis=deep_analysis,
            decision=recommendation.decision,
            action_type=recommendation.action_type,
            rationale=recommendation.rationale,
            monitoring_note=recommendation.monitoring_note,
            risk_metrics=risk.model_dump(mode="json"),
        )
        return decision

    def _on_ibkr_error(self, payload: dict) -> None:
        code = payload.get("error_code")
        req_id = payload.get("req_id")
        msg = payload.get("error_string", "")
        log_level = payload.get("level", "error")
        message = f"IBKR API code={code} req_id={req_id}: {msg}"
        if log_level in {"info", "warning"}:
            self.logger.info(message, **payload)
            return
        self.logger.error(message, **payload)

    def _on_connectivity_progress(self, payload: dict) -> None:
        self.logger.info("IBKR connectivity progress", **payload)


def run_command(service: AdvisorService) -> None:
    scheduler = BlockingScheduler(timezone="UTC")

    def _job() -> None:
        try:
            service.collect_cycle()
        except Exception as exc:
            service.logger.error("Cycle failed", error=str(exc))
            service.store.heartbeat("advisor", "error", {"error": str(exc)})

    service.start()
    _job()
    scheduler.add_job(
        _job,
        "interval",
        seconds=service.config.run_interval_seconds,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        scheduler.shutdown(wait=False)
        service.stop()


def once_command(service: AdvisorService) -> None:
    service.start()
    try:
        decision = service.collect_cycle()
        service.logger.info(
            "Single cycle decision",
            decision=decision.recommendation.decision,
            action_type=decision.recommendation.action_type,
        )
    finally:
        service.stop()


def doctor_command(config: AppConfig) -> int:
    logger = StructuredLogger(config.json_log_path)
    failed = False

    if not config.ibkr_account_id:
        logger.error("IBKR_ACCOUNT_ID is empty")
        failed = True

    if not config.openai_enabled:
        logger.error("OPENAI_API_KEY is empty")
        failed = True

    # DB check
    try:
        PostgresStore(config.postgres_dsn).doctor()
        logger.info("PostgreSQL connection ok")
    except Exception as exc:
        logger.error("PostgreSQL connection failed", error=str(exc))
        failed = True

    # OpenAI auth check
    if config.openai_enabled:
        try:
            from openai import OpenAI

            client = OpenAI(api_key=config.openai_api_key)
            _ = client.models.list()
            logger.info("OpenAI authentication ok")
        except Exception as exc:
            logger.error("OpenAI authentication failed", error=str(exc))
            failed = True

    # IBKR socket check
    try:
        with socket.create_connection((config.ibkr_host, config.ibkr_port), timeout=3):
            pass
        logger.info("IBKR socket reachable", host=config.ibkr_host, port=config.ibkr_port)
    except Exception as exc:
        logger.error("IBKR socket check failed", error=str(exc))
        failed = True

    return 1 if failed else 0


def chat_command(config: AppConfig, question: str | None) -> int:
    logger = StructuredLogger(config.json_log_path)
    store = PostgresStore(config.postgres_dsn)
    ai = AIAnalyzer(config)

    try:
        latest = store.latest_decision(config.ibkr_account_id or None)
    except Exception as exc:
        logger.error("Unable to load latest decision from database", error=str(exc))
        return 1

    if latest is None:
        logger.error("No prior decisions found; run `advisor once` or `advisor run` first.")
        return 1

    history: List[Dict[str, str]] = []
    conversation_id = str(uuid4())
    turn_index = 0
    known_symbols = _known_symbols_for_followup(config.watchlist, latest)

    def _ask(user_question: str) -> None:
        nonlocal turn_index
        now_utc = datetime.now(timezone.utc)
        instrument_symbols = _symbols_for_history(user_question, known_symbols)
        history_by_symbol = store.instrument_history(symbols=instrument_symbols, since_ts=now_utc - timedelta(days=7))
        followup_market_context = build_followup_market_context(
            question=user_question,
            known_symbols=known_symbols,
            history_by_symbol=history_by_symbol,
            now=now_utc,
        )
        latest_with_context = {
            **latest,
            "followup_market_context": followup_market_context,
        }

        answer, model_used = ai.answer_follow_up(user_question, latest_with_context, history)
        print(f"advisor ({model_used})> {answer}")
        history.append({"role": "user", "content": user_question})
        history.append({"role": "assistant", "content": answer})
        turn_index += 1
        try:
            store.write_followup_turn(
                conversation_id=conversation_id,
                turn_index=turn_index,
                model_used=model_used,
                user_question=user_question,
                assistant_answer=answer,
                account_id=latest.get("account_id"),
                decision_cycle_ts=latest.get("cycle_ts"),
                context_payload={
                    "latest_recommendation": latest.get("recommendation_payload"),
                    "latest_request": latest.get("request_payload"),
                    "followup_market_context": followup_market_context,
                },
            )
        except Exception as exc:
            logger.error("Failed to persist follow-up chat turn", error=str(exc), conversation_id=conversation_id)
        logger.info("Follow-up chat turn", model_used=model_used, question=user_question, answer=answer)

    if question:
        _ask(question)
        return 0

    print("Follow-up chat started. Type your question and press Enter. Type `exit` to quit.")
    print(f"Conversation ID: {conversation_id}")
    print(f"Loaded latest recommendation from cycle: {latest.get('cycle_ts')}")
    while True:
        try:
            user_question = input("you> ").strip()
        except EOFError:
            print()
            break
        if not user_question:
            continue
        if user_question.lower() in {"exit", "quit"}:
            break
        _ask(user_question)
    return 0


def _known_symbols_for_followup(watchlist: List[str], latest: Dict[str, object]) -> List[str]:
    symbols: set[str] = set()
    for entry in watchlist:
        canonical = canonical_instrument_key(entry)
        if canonical:
            symbols.add(canonical)

    request_payload = latest.get("request_payload")
    if isinstance(request_payload, dict):
        key_instruments = request_payload.get("key_instruments")
        if isinstance(key_instruments, list):
            for item in key_instruments:
                if not isinstance(item, dict):
                    continue
                symbol = item.get("symbol")
                if isinstance(symbol, str):
                    canonical = canonical_instrument_key(symbol)
                    if canonical:
                        symbols.add(canonical)

    recommendation_payload = latest.get("recommendation_payload")
    if isinstance(recommendation_payload, dict):
        target_symbols = recommendation_payload.get("target_symbols")
        if isinstance(target_symbols, list):
            for symbol in target_symbols:
                if isinstance(symbol, str):
                    canonical = canonical_instrument_key(symbol)
                    if canonical:
                        symbols.add(canonical)

    return sorted(symbols)


def _symbols_for_history(question: str, known_symbols: List[str]) -> List[str]:
    requested = extract_requested_instruments(question, known_symbols)
    return sorted(set(requested))


def main() -> None:
    parser = argparse.ArgumentParser(description="IBKR + OpenAI portfolio advisor")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run")
    sub.add_parser("once")
    sub.add_parser("doctor")
    chat_parser = sub.add_parser("chat")
    chat_parser.add_argument("--question", type=str, default=None, help="Single follow-up question")

    args = parser.parse_args()
    config = AppConfig.from_env()

    if args.command == "doctor":
        raise SystemExit(doctor_command(config))
    if args.command == "chat":
        raise SystemExit(chat_command(config, args.question))

    service = AdvisorService(config)
    if args.command == "once":
        once_command(service)
    elif args.command == "run":
        run_command(service)
    else:
        raise SystemExit("Unknown command")


if __name__ == "__main__":
    main()
