from __future__ import annotations

import argparse
import socket
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler

from advisor.ai.langchain_flow import AIAnalyzer
from advisor.config import AppConfig
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
        self.ibkr = IBKRClient(config)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="IBKR + OpenAI portfolio advisor")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run")
    sub.add_parser("once")
    sub.add_parser("doctor")

    args = parser.parse_args()
    config = AppConfig.from_env()

    if args.command == "doctor":
        raise SystemExit(doctor_command(config))

    service = AdvisorService(config)
    if args.command == "once":
        once_command(service)
    elif args.command == "run":
        run_command(service)
    else:
        raise SystemExit("Unknown command")


if __name__ == "__main__":
    main()
