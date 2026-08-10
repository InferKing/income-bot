from datetime import UTC, datetime, timedelta
from decimal import Decimal

from income_tg.backtest import (
    Bar,
    MeanReversionStrategy,
    MovingAverageTrendStrategy,
    StrategyContext,
)


def context(prices: list[str], current_target: str = "0") -> StrategyContext:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    market = tuple(
        Bar(
            start + timedelta(hours=index),
            "BTCUSDT",
            Decimal(price),
            Decimal(price),
            Decimal(price),
            Decimal(price),
            Decimal("1"),
        )
        for index, price in enumerate(prices)
    )
    return StrategyContext(
        "BTCUSDT",
        market[-1].timestamp,
        market,
        Decimal("100"),
        Decimal("100"),
        Decimal("0"),
        Decimal(current_target),
    )


def test_moving_average_trend_enters_and_exits() -> None:
    strategy = MovingAverageTrendStrategy(short_window=2, long_window=3)
    strategy.reset()
    enter = strategy.on_bar(context(["1", "2", "4"]))
    assert enter is not None and enter.target_exposure == Decimal("1")

    exit_signal = strategy.on_bar(context(["4", "2", "1"], "1"))
    assert exit_signal is not None and exit_signal.target_exposure == Decimal("0")


def test_mean_reversion_trades_extreme_and_exits_near_mean() -> None:
    strategy = MeanReversionStrategy(window=3, entry_z=1.0, exit_z=0.5)
    strategy.reset()
    enter = strategy.on_bar(context(["10", "10", "8"]))
    assert enter is not None and enter.target_exposure == Decimal("1")

    exit_signal = strategy.on_bar(context(["9", "11", "10"], "1"))
    assert exit_signal is not None and exit_signal.target_exposure == Decimal("0")
