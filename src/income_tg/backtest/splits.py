from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import pairwise

from income_tg.backtest.engine import BacktestEngine
from income_tg.backtest.models import BacktestResult, Bar, Strategy


@dataclass(frozen=True, slots=True)
class ChronologicalSplit:
    train: tuple[Bar, ...]
    validation: tuple[Bar, ...]
    test: tuple[Bar, ...]


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    train: tuple[Bar, ...]
    test: tuple[Bar, ...]


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    window: WalkForwardWindow
    result: BacktestResult


def chronological_split(
    bars: Sequence[Bar], *, train_fraction: float = 0.6, validation_fraction: float = 0.2
) -> ChronologicalSplit:
    market = _validated_bars(bars)
    if not 0 < train_fraction < 1:
        raise ValueError("train fraction must be between zero and one")
    if not 0 < validation_fraction < 1:
        raise ValueError("validation fraction must be between zero and one")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("train and validation fractions must leave a test segment")
    train_end = int(len(market) * train_fraction)
    validation_end = train_end + int(len(market) * validation_fraction)
    if train_end == 0 or validation_end == train_end or validation_end == len(market):
        raise ValueError("dataset is too short for non-empty train/validation/test segments")
    return ChronologicalSplit(
        train=market[:train_end],
        validation=market[train_end:validation_end],
        test=market[validation_end:],
    )


def walk_forward_windows(
    bars: Sequence[Bar],
    *,
    train_size: int,
    test_size: int,
    step_size: int | None = None,
    expanding: bool = False,
) -> tuple[WalkForwardWindow, ...]:
    market = _validated_bars(bars)
    if train_size <= 0 or test_size <= 0:
        raise ValueError("train and test sizes must be positive")
    step = test_size if step_size is None else step_size
    if step <= 0:
        raise ValueError("step size must be positive")
    windows: list[WalkForwardWindow] = []
    test_start = train_size
    while test_start + test_size <= len(market):
        train_start = 0 if expanding else test_start - train_size
        windows.append(
            WalkForwardWindow(
                train=market[train_start:test_start],
                test=market[test_start : test_start + test_size],
            )
        )
        test_start += step
    return tuple(windows)


def run_walk_forward(
    engine: BacktestEngine,
    windows: Sequence[WalkForwardWindow],
    strategy_factory: Callable[[tuple[Bar, ...]], Strategy],
) -> tuple[WalkForwardFold, ...]:
    """Fit/create on each train tuple, then expose only its later test tuple to the engine."""

    folds: list[WalkForwardFold] = []
    for window in windows:
        if window.train[-1].timestamp >= window.test[0].timestamp:
            raise ValueError("walk-forward train data must precede test data")
        strategy = strategy_factory(window.train)
        folds.append(
            WalkForwardFold(window, engine.run(window.test, strategy, history=window.train))
        )
    return tuple(folds)


def _validated_bars(bars: Sequence[Bar]) -> tuple[Bar, ...]:
    market = tuple(bars)
    if len(market) < 3:
        raise ValueError("at least three bars are required")
    symbol = market[0].symbol
    for previous, current in pairwise(market):
        if current.symbol != symbol:
            raise ValueError("splits accept one symbol")
        if current.timestamp <= previous.timestamp:
            raise ValueError("bars must be strictly chronological and unique")
    return market
