import pytest

from advisor.ai.langchain_flow import fallback_recommendation, parse_recommendation
from advisor.models import ActionType, DecisionType


def test_valid_recommendation_parse() -> None:
    payload = """
    {
      "decision": "NO_ACTION",
      "action_type": "HOLD",
      "target_symbols": [],
      "rationale": "No high-confidence setup right now.",
      "risk_checks": {
        "margin_ok": true,
        "leverage_ok": true,
        "concentration_ok": true
      },
      "confidence": 0.41,
      "ttl_minutes": 10,
      "monitoring_note": "Keep monitoring."
    }
    """
    recommendation = parse_recommendation(payload)
    assert recommendation.decision == DecisionType.NO_ACTION
    assert recommendation.action_type == ActionType.HOLD


def test_parse_failure_and_fallback() -> None:
    with pytest.raises(Exception):
        parse_recommendation("not-json")

    fallback = fallback_recommendation("parser error")
    assert fallback.decision == DecisionType.NO_ACTION
    assert fallback.action_type == ActionType.HOLD
    assert "parser error" in fallback.monitoring_note
