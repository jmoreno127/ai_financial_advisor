from advisor.engine.risk_policy import apply_balanced_swing_policy
from advisor.models import ActionType, Recommendation, RiskChecks, RiskMetrics


def _risk(margin: float, cushion: float, concentration_ok: bool, drawdown_ok: bool = True) -> RiskMetrics:
    return RiskMetrics(
        gross_leverage=1.5,
        margin_utilization=margin,
        cushion=cushion,
        largest_position_weight=0.2,
        drawdown_from_day_high=0.01,
        margin_ok=margin <= 0.68,
        leverage_ok=True,
        concentration_ok=concentration_ok,
        drawdown_ok=drawdown_ok,
        near_breach=False,
        breaches=[],
    )


def _reco(action: ActionType) -> Recommendation:
    return Recommendation(
        decision="SUGGEST_ACTION",
        action_type=action,
        target_symbols=["AAPL"],
        rationale="Test",
        risk_checks=RiskChecks(margin_ok=True, leverage_ok=True, concentration_ok=True),
        confidence=0.8,
        ttl_minutes=15,
        monitoring_note="",
    )


def test_derisk_when_margin_breached() -> None:
    risk = _risk(margin=0.75, cushion=0.10, concentration_ok=True)
    reco = _reco(ActionType.REDUCE)
    out = apply_balanced_swing_policy(reco, risk)
    assert "Risk guard active" in out.monitoring_note


def test_hold_when_add_not_allowed() -> None:
    risk = _risk(margin=0.60, cushion=0.20, concentration_ok=False)
    reco = _reco(ActionType.ADD)
    out = apply_balanced_swing_policy(reco, risk)
    assert out.action_type == ActionType.HOLD
