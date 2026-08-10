import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from income_tg.backtest.metrics import calculate_metrics
from income_tg.backtest.models import ClosedTrade, EquityPoint


def test_metrics_cover_returns_drawdown_trades_and_risk_ratios() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    curve = [
        EquityPoint(start, Decimal("110")),
        EquityPoint(start + timedelta(hours=1), Decimal("88")),
        EquityPoint(start + timedelta(hours=2), Decimal("120")),
    ]
    trades = [
        ClosedTrade(start, start, Decimal("1"), Decimal("1"), Decimal("1"), Decimal("10")),
        ClosedTrade(start, start, Decimal("1"), Decimal("1"), Decimal("1"), Decimal("-5")),
    ]

    metrics = calculate_metrics(Decimal("100"), curve, trades, Decimal("2"), 365)

    assert metrics.net_return == pytest.approx(0.2)
    assert metrics.max_drawdown == pytest.approx(0.2)
    assert metrics.profit_factor == pytest.approx(2.0)
    assert metrics.expectancy == pytest.approx(2.5)
    assert math.isfinite(metrics.sharpe)
    assert math.isfinite(metrics.sortino)
    assert metrics.trade_count == 2
    assert metrics.total_fees == pytest.approx(2.0)


def test_no_losses_has_infinite_profit_factor() -> None:
    at = datetime(2026, 1, 1, tzinfo=UTC)
    trade = ClosedTrade(at, at, Decimal("1"), Decimal("1"), Decimal("2"), Decimal("1"))
    metrics = calculate_metrics(
        Decimal("100"), [EquityPoint(at, Decimal("101"))], [trade], Decimal("0"), 1
    )
    assert metrics.profit_factor == math.inf
