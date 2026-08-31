from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from income_tg.models.dataset import classify_forward_return
from income_tg.models.evaluation import evaluate_admission, probability_metrics
from income_tg.models.registry import FileModelRegistry
from income_tg.models.training import ChronologicalDataset, train_ensemble


def _dataset(samples: int = 120) -> ChronologicalDataset:
    random = np.random.default_rng(42)
    first = random.normal(size=samples)
    second = random.normal(scale=0.5, size=samples)
    score = first + second
    targets = np.where(score > 0.5, 1, np.where(score < -0.5, -1, 0)).astype(np.int64)
    features = np.column_stack((first, second)).astype(np.float64)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return ChronologicalDataset(
        timestamps=tuple(start + timedelta(minutes=index) for index in range(samples)),
        feature_names=("momentum", "flow"),
        features=features,
        targets=targets,
    )


def test_ensemble_trains_calibrates_and_predicts() -> None:
    dataset = _dataset()
    model = train_ensemble(dataset)
    prediction = model.predict(
        as_of=dataset.timestamps[-1],
        feature_names=dataset.feature_names,
        values=tuple(float(value) for value in dataset.features[-1]),
    )
    assert 0 <= prediction.probability_up <= 1
    assert 0 <= prediction.probability_no_trade <= 1
    assert (
        prediction.probability_up + prediction.probability_down + prediction.probability_no_trade
        == pytest.approx(1)
    )
    assert 0 <= prediction.confidence <= 1
    assert 0 < model.confidence_threshold < 1
    assert len(prediction.contributions) == 2


@pytest.mark.parametrize(
    ("forward_return", "expected"),
    [(-0.003, -1), (-0.002, 0), (0.0, 0), (0.002, 0), (0.003, 1)],
)
def test_cost_aware_target_has_no_trade_zone(forward_return: float, expected: int) -> None:
    assert classify_forward_return(forward_return, 0.002) == expected


def test_registry_detects_artifact_and_promotes(tmp_path: Path) -> None:
    model = train_ensemble(_dataset())
    registry = FileModelRegistry(tmp_path)
    registered = registry.register(model)
    assert registered.stage == "CHALLENGER"
    registry.promote(model.version)
    loaded = registry.load_champion()
    assert loaded.version == model.version


def test_probability_metrics_and_admission() -> None:
    metrics = probability_metrics([0.9, 0.2, 0.8, 0.1], [1, 0, 1, 0])
    assert metrics.accuracy == 1
    assert metrics.brier_score < 0.1
    decision = evaluate_admission(
        net_return=0.2,
        max_drawdown=0.1,
        profit_factor=1.5,
        closed_trades=120,
        test_samples=500,
        actionable_labels=200,
        profitable_walk_forward_windows=3,
        walk_forward_windows=4,
        beats_baseline=True,
        recent_period_stable=True,
    )
    assert decision.accepted is True


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_admission_rejects_non_finite_metrics(invalid: float) -> None:
    decision = evaluate_admission(
        net_return=invalid,
        max_drawdown=invalid,
        profit_factor=invalid,
        closed_trades=120,
        test_samples=500,
        actionable_labels=200,
        profitable_walk_forward_windows=3,
        walk_forward_windows=4,
        beats_baseline=True,
        beats_champion=True,
        recent_period_stable=True,
    )
    assert decision.accepted is False
    assert decision.reasons == ("INVALID_METRICS",)


def test_admission_requires_twenty_trades_or_twenty_percent_of_actionable_labels() -> None:
    accepted = evaluate_admission(
        net_return=0.2,
        max_drawdown=0.1,
        profit_factor=1.5,
        closed_trades=24,
        test_samples=159,
        actionable_labels=120,
        profitable_walk_forward_windows=3,
        walk_forward_windows=4,
        beats_baseline=True,
        recent_period_stable=True,
    )
    rejected = evaluate_admission(
        net_return=0.2,
        max_drawdown=0.1,
        profit_factor=1.5,
        closed_trades=23,
        test_samples=159,
        actionable_labels=120,
        profitable_walk_forward_windows=3,
        walk_forward_windows=4,
        beats_baseline=True,
        recent_period_stable=True,
    )

    assert accepted.accepted is True
    assert rejected.reasons == ("NOT_ENOUGH_TRADES",)


def test_admission_requires_thirty_actionable_labels_and_three_profitable_windows() -> None:
    decision = evaluate_admission(
        net_return=0.2,
        max_drawdown=0.1,
        profit_factor=1.5,
        closed_trades=20,
        test_samples=159,
        actionable_labels=29,
        profitable_walk_forward_windows=2,
        walk_forward_windows=4,
        beats_baseline=True,
        recent_period_stable=True,
    )

    assert decision.reasons == (
        "INSUFFICIENT_ACTIONABLE_LABELS",
        "NOT_ENOUGH_PROFITABLE_WINDOWS",
    )


def test_probability_metrics_rejects_non_finite_probability() -> None:
    with pytest.raises(ValueError, match="finite"):
        probability_metrics([float("nan")], [1])


def test_dataset_rejects_non_chronological_rows() -> None:
    dataset = _dataset()
    invalid = ChronologicalDataset(
        timestamps=tuple(reversed(dataset.timestamps)),
        feature_names=dataset.feature_names,
        features=dataset.features,
        targets=dataset.targets,
    )
    with pytest.raises(ValueError, match="упорядочены"):
        invalid.validate()
