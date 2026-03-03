from __future__ import annotations

from advisor.models import ActionType, DecisionType, Recommendation, RiskMetrics


def apply_balanced_swing_policy(recommendation: Recommendation, risk: RiskMetrics) -> Recommendation:
    if recommendation.action_type == ActionType.ADD:
        can_add = (
            risk.margin_utilization < 0.55
            and risk.cushion > 0.30
            and risk.concentration_ok
            and risk.leverage_ok
        )
        if not can_add:
            recommendation.action_type = ActionType.HOLD
            recommendation.monitoring_note = (
                recommendation.monitoring_note
                or "Leverage increase blocked by balanced swing policy constraints."
            )

    if not risk.margin_ok or not risk.drawdown_ok:
        recommendation.decision = DecisionType.SUGGEST_ACTION
        recommendation.action_type = ActionType.REDUCE
        recommendation.monitoring_note = (
            recommendation.monitoring_note
            or "Risk guard active (margin/drawdown); de-risk action is preferred."
        )

    recommendation.risk_checks.margin_ok = risk.margin_ok
    recommendation.risk_checks.leverage_ok = risk.leverage_ok
    recommendation.risk_checks.concentration_ok = risk.concentration_ok
    return recommendation
