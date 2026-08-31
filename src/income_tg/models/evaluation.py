from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProbabilityMetrics:
    accuracy: float
    brier_score: float
    log_loss: float
    samples: int


@dataclass(frozen=True, slots=True)
class AdmissionCriteria:
    max_drawdown: float = 0.15
    min_profit_factor: float = 1.2
    min_actionable_labels: int = 30
    min_closed_trades: int = 20
    min_closed_trade_fraction: float = 0.20
    walk_forward_windows: int = 4
    min_profitable_walk_forward_windows: int = 3

    def __post_init__(self) -> None:
        if self.min_actionable_labels <= 0:
            raise ValueError("min_actionable_labels must be positive")
        if self.min_closed_trades <= 0:
            raise ValueError("min_closed_trades must be positive")
        if not math.isfinite(self.min_closed_trade_fraction) or not (
            0 < self.min_closed_trade_fraction <= 1
        ):
            raise ValueError("min_closed_trade_fraction must be between zero and one")
        if self.walk_forward_windows <= 0:
            raise ValueError("walk_forward_windows must be positive")
        if not 0 < self.min_profitable_walk_forward_windows <= self.walk_forward_windows:
            raise ValueError(
                "min_profitable_walk_forward_windows must be between one and walk_forward_windows"
            )

    def required_closed_trades(self, actionable_labels: int) -> int:
        if actionable_labels < 0:
            raise ValueError("actionable_labels must be non-negative")
        return max(
            self.min_closed_trades,
            math.ceil(actionable_labels * self.min_closed_trade_fraction),
        )


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    accepted: bool
    reasons: tuple[str, ...]


def probability_metrics(
    probabilities_up: Sequence[float], targets: Sequence[int]
) -> ProbabilityMetrics:
    if not probabilities_up or len(probabilities_up) != len(targets):
        raise ValueError("Вероятности и цели должны быть непустыми и одинаковой длины")
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in probabilities_up):
        raise ValueError("probabilities must be finite values between zero and one")
    clipped = [min(max(value, 1e-12), 1 - 1e-12) for value in probabilities_up]
    if any(target not in (0, 1) for target in targets):
        raise ValueError("Цели должны быть бинарными")
    accuracy = sum(
        (probability >= 0.5) == bool(target)
        for probability, target in zip(clipped, targets, strict=True)
    ) / len(targets)
    brier = sum(
        (probability - target) ** 2 for probability, target in zip(clipped, targets, strict=True)
    ) / len(targets)
    loss = -sum(
        target * math.log(probability) + (1 - target) * math.log(1 - probability)
        for probability, target in zip(clipped, targets, strict=True)
    ) / len(targets)
    return ProbabilityMetrics(
        accuracy=accuracy, brier_score=brier, log_loss=loss, samples=len(targets)
    )


def evaluate_admission(
    *,
    net_return: float,
    max_drawdown: float,
    profit_factor: float,
    closed_trades: int,
    test_samples: int,
    actionable_labels: int,
    profitable_walk_forward_windows: int,
    walk_forward_windows: int,
    beats_baseline: bool,
    recent_period_stable: bool,
    beats_champion: bool = True,
    criteria: AdmissionCriteria | None = None,
) -> AdmissionDecision:
    active_criteria = criteria or AdmissionCriteria()
    reasons: list[str] = []
    if (
        not all(math.isfinite(value) for value in (net_return, max_drawdown, profit_factor))
        or max_drawdown < 0
        or closed_trades < 0
        or test_samples <= 0
        or closed_trades > test_samples
        or actionable_labels < 0
        or actionable_labels > test_samples
        or walk_forward_windows <= 0
        or profitable_walk_forward_windows < 0
        or profitable_walk_forward_windows > walk_forward_windows
    ):
        return AdmissionDecision(False, ("INVALID_METRICS",))
    if actionable_labels < active_criteria.min_actionable_labels:
        reasons.append("INSUFFICIENT_ACTIONABLE_LABELS")
    if net_return <= 0:
        reasons.append("NET_RETURN_NOT_POSITIVE")
    if max_drawdown > active_criteria.max_drawdown:
        reasons.append("MAX_DRAWDOWN_EXCEEDED")
    if profit_factor < active_criteria.min_profit_factor:
        reasons.append("PROFIT_FACTOR_TOO_LOW")
    if closed_trades < active_criteria.required_closed_trades(actionable_labels):
        reasons.append("NOT_ENOUGH_TRADES")
    if (
        walk_forward_windows != active_criteria.walk_forward_windows
        or profitable_walk_forward_windows < active_criteria.min_profitable_walk_forward_windows
    ):
        reasons.append("NOT_ENOUGH_PROFITABLE_WINDOWS")
    if not beats_baseline:
        reasons.append("DOES_NOT_BEAT_BASELINE")
    if not beats_champion:
        reasons.append("DOES_NOT_BEAT_CHAMPION")
    if not recent_period_stable:
        reasons.append("RECENT_PERIOD_UNSTABLE")
    return AdmissionDecision(accepted=not reasons, reasons=tuple(reasons))
