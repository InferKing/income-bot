from datetime import UTC, datetime, timedelta
from decimal import Decimal

from income_tg.backtest import (
    BacktestConfig,
    BacktestEngine,
    Bar,
    BuyAndHoldStrategy,
    chronological_split,
    run_walk_forward,
    walk_forward_windows,
)


def bars(count: int) -> list[Bar]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Bar(
            start + timedelta(hours=index),
            "BTCUSDT",
            Decimal(100 + index),
            Decimal(100 + index),
            Decimal(100 + index),
            Decimal(100 + index),
            Decimal("1"),
        )
        for index in range(count)
    ]


def test_chronological_split_has_strict_non_overlapping_boundaries() -> None:
    split = chronological_split(bars(10), train_fraction=0.6, validation_fraction=0.2)

    assert (len(split.train), len(split.validation), len(split.test)) == (6, 2, 2)
    assert split.train[-1].timestamp < split.validation[0].timestamp
    assert split.validation[-1].timestamp < split.test[0].timestamp


def test_rolling_and_expanding_walk_forward_windows() -> None:
    market = bars(12)
    rolling = walk_forward_windows(market, train_size=4, test_size=2)
    expanding = walk_forward_windows(market, train_size=4, test_size=2, expanding=True)

    assert [len(window.train) for window in rolling] == [4, 4, 4, 4]
    assert [len(window.train) for window in expanding] == [4, 6, 8, 10]
    assert all(window.train[-1].timestamp < window.test[0].timestamp for window in rolling)


def test_walk_forward_factory_receives_train_only() -> None:
    windows = walk_forward_windows(bars(8), train_size=4, test_size=2)
    training_ends = []

    def factory(train: tuple[Bar, ...]) -> BuyAndHoldStrategy:
        training_ends.append(train[-1].timestamp)
        return BuyAndHoldStrategy()

    engine = BacktestEngine(BacktestConfig(fee_rate=Decimal("0"), slippage_bps=Decimal("0")))
    folds = run_walk_forward(engine, windows, factory)

    assert len(folds) == 2
    assert training_ends == [window.train[-1].timestamp for window in windows]
    assert all(
        fold.result.equity_curve[0].timestamp == fold.window.test[0].timestamp for fold in folds
    )
    assert all(
        fold.result.executions[0].timestamp == fold.window.test[0].timestamp for fold in folds
    )
