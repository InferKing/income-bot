from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from decimal import Decimal
from itertools import pairwise

from income_tg.backtest.models import ClosedTrade, EquityPoint, PerformanceMetrics


def calculate_metrics(
    initial_cash: Decimal,
    equity_curve: Sequence[EquityPoint],
    trades: Sequence[ClosedTrade],
    total_fees: Decimal,
    periods_per_year: int,
) -> PerformanceMetrics:
    if initial_cash <= 0:
        raise ValueError("initial cash must be positive")
    if periods_per_year <= 0:
        raise ValueError("periods per year must be positive")

    equities = [float(initial_cash), *(float(point.equity) for point in equity_curve)]
    final_equity = equities[-1]
    returns = [current / previous - 1.0 for previous, current in pairwise(equities) if previous > 0]

    peak = equities[0]
    max_drawdown = 0.0
    for equity in equities:
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)

    pnls = [float(trade.pnl) for trade in trades]
    gross_profit = sum(pnl for pnl in pnls if pnl > 0)
    gross_loss = -sum(pnl for pnl in pnls if pnl < 0)
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = math.inf
    else:
        profit_factor = 0.0

    expectancy = statistics.fmean(pnls) if pnls else 0.0
    sharpe = _annualized_ratio(returns, periods_per_year, downside_only=False)
    sortino = _annualized_ratio(returns, periods_per_year, downside_only=True)
    return PerformanceMetrics(
        net_return=final_equity / float(initial_cash) - 1.0,
        max_drawdown=max_drawdown,
        profit_factor=profit_factor,
        expectancy=expectancy,
        sharpe=sharpe,
        sortino=sortino,
        trade_count=len(trades),
        total_fees=float(total_fees),
    )


def _annualized_ratio(
    returns: Sequence[float], periods_per_year: int, *, downside_only: bool
) -> float:
    if not returns:
        return 0.0
    mean = statistics.fmean(returns)
    if downside_only:
        deviation = math.sqrt(statistics.fmean(min(value, 0.0) ** 2 for value in returns))
    else:
        deviation = statistics.pstdev(returns)
    if deviation == 0:
        if mean > 0:
            return math.inf
        if mean < 0:
            return -math.inf
        return 0.0
    return mean / deviation * math.sqrt(periods_per_year)
