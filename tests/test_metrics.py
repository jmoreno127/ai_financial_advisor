from datetime import datetime, timezone

from advisor.engine.metrics import compute_risk_metrics
from advisor.models import PortfolioSnapshot, PositionSnapshot


def _portfolio(net_liq: float, init_margin: float, excess_liq: float) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        cycle_ts=datetime.now(timezone.utc),
        account_id="DU123",
        net_liquidation=net_liq,
        init_margin_req=init_margin,
        excess_liquidity=excess_liq,
        gross_position_value=120_000,
        daily_pnl=1_200,
        total_unrealized_pnl=900,
        day_high_equity=110_000,
        positions=[
            PositionSnapshot(symbol="AAPL", quantity=100, market_value=25_000, market_price=250),
            PositionSnapshot(symbol="NVDA", quantity=80, market_value=35_000, market_price=437.5),
        ],
    )


def test_compute_risk_metrics_normal() -> None:
    portfolio = _portfolio(100_000, 40_000, 45_000)
    risk = compute_risk_metrics(
        portfolio,
        max_margin_utilization=0.68,
        max_single_name_exposure=0.50,
        max_gross_leverage=2.2,
        max_drawdown_from_day_high=0.10,
    )

    assert round(risk.gross_leverage, 2) == 1.20
    assert round(risk.margin_utilization, 2) == 0.40
    assert round(risk.cushion, 2) == 0.45
    assert risk.margin_ok is True
    assert risk.leverage_ok is True
    assert risk.concentration_ok is True


def test_compute_risk_metrics_breaches() -> None:
    portfolio = _portfolio(100_000, 85_000, 5_000)
    portfolio.gross_position_value = 250_000
    portfolio.positions[0].market_value = 70_000
    risk = compute_risk_metrics(
        portfolio,
        max_margin_utilization=0.68,
        max_single_name_exposure=0.22,
        max_gross_leverage=2.2,
        max_drawdown_from_day_high=0.04,
    )

    assert risk.margin_ok is False
    assert risk.leverage_ok is False
    assert risk.concentration_ok is False
    assert "margin" in risk.breaches
    assert "leverage" in risk.breaches
    assert "concentration" in risk.breaches
