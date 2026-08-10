from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from decimal import Decimal

from income_tg.backtest.models import StrategyContext, TargetSignal


@dataclass(slots=True)
class BuyAndHoldStrategy:
    exposure: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        _validate_exposure(self.exposure)

    def reset(self) -> None:
        pass

    def on_bar(self, context: StrategyContext) -> TargetSignal | None:
        if context.current_target == self.exposure:
            return None
        return TargetSignal(self.exposure, context.as_of, "buy and hold")


@dataclass(slots=True)
class MovingAverageTrendStrategy:
    short_window: int = 20
    long_window: int = 50
    exposure: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if self.short_window <= 0 or self.long_window <= self.short_window:
            raise ValueError("moving-average windows must satisfy 0 < short < long")
        _validate_exposure(self.exposure)

    def reset(self) -> None:
        pass

    def on_bar(self, context: StrategyContext) -> TargetSignal | None:
        if len(context.bars) < self.long_window:
            return None
        closes = [bar.close for bar in context.bars]
        short_average = sum(closes[-self.short_window :]) / self.short_window
        long_average = sum(closes[-self.long_window :]) / self.long_window
        target = self.exposure if short_average > long_average else Decimal("0")
        if target == context.current_target:
            return None
        return TargetSignal(target, context.as_of, "moving-average crossover")


@dataclass(slots=True)
class MeanReversionStrategy:
    window: int = 20
    entry_z: float = 2.0
    exit_z: float = 0.5
    exposure: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if self.window < 2:
            raise ValueError("mean-reversion window must be at least 2")
        if not math.isfinite(self.entry_z) or self.entry_z <= 0:
            raise ValueError("entry z-score must be finite and positive")
        if not math.isfinite(self.exit_z) or not 0 <= self.exit_z < self.entry_z:
            raise ValueError("exit z-score must be finite and between zero and entry z-score")
        _validate_exposure(self.exposure)

    def reset(self) -> None:
        pass

    def on_bar(self, context: StrategyContext) -> TargetSignal | None:
        if len(context.bars) < self.window:
            return None
        values = [float(bar.close) for bar in context.bars[-self.window :]]
        deviation = statistics.pstdev(values)
        if deviation == 0:
            target = Decimal("0")
        else:
            z_score = (values[-1] - statistics.fmean(values)) / deviation
            if z_score <= -self.entry_z:
                target = self.exposure
            elif z_score >= self.entry_z:
                target = -self.exposure
            elif abs(z_score) <= self.exit_z:
                target = Decimal("0")
            else:
                target = context.current_target
        if target == context.current_target:
            return None
        return TargetSignal(target, context.as_of, "mean reversion z-score")


def _validate_exposure(exposure: Decimal) -> None:
    if not exposure.is_finite() or exposure <= 0:
        raise ValueError("strategy exposure must be finite and positive")
