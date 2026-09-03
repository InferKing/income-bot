from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import numpy as np
import pytest

from income_tg.jobs.database_training import (
    TrainingTarget,
    _model_training_partitions,
    _select_confidence_threshold,
    _strategy_metrics,
)
from income_tg.models.dataset import LabeledDataset
from income_tg.models.evaluation import AdmissionCriteria
from income_tg.models.inference import EnsembleModel, ModelPrediction
from income_tg.models.training import ChronologicalDataset


class PredictableModel:
    feature_names = ("signal",)
    confidence_threshold = 0.70

    def __init__(self, probabilities_up: tuple[float, ...]) -> None:
        self._probabilities = iter(probabilities_up)

    def predict(
        self,
        *,
        as_of: datetime,
        feature_names: tuple[str, ...],
        values: tuple[float, ...],
    ) -> ModelPrediction:
        del feature_names, values
        probability_up = next(self._probabilities)
        return ModelPrediction(
            as_of=as_of,
            probability_up=probability_up,
            probability_down=1 - probability_up,
            probability_no_trade=0.0,
            confidence=max(probability_up, 1 - probability_up),
            expected_directional_score=probability_up - 0.5,
            contributions=(),
            model_version="candidate",
        )


class FeatureProbabilityModel:
    feature_names = ("signal",)
    confidence_threshold = 0.70

    def predict(
        self,
        *,
        as_of: datetime,
        feature_names: tuple[str, ...],
        values: tuple[float, ...],
    ) -> ModelPrediction:
        del feature_names
        probability_up = values[0]
        return ModelPrediction(
            as_of=as_of,
            probability_up=probability_up,
            probability_down=1 - probability_up,
            probability_no_trade=0.0,
            confidence=max(probability_up, 1 - probability_up),
            expected_directional_score=probability_up - 0.5,
            contributions=(),
            model_version="candidate",
        )


def test_threshold_validation_is_disjoint_from_model_training_and_final_test() -> None:
    start = datetime(2026, 8, 24, 10, tzinfo=UTC)
    timestamps = tuple(start + timedelta(minutes=index) for index in range(100))
    labeled = LabeledDataset(
        dataset=ChronologicalDataset(
            timestamps=timestamps,
            feature_names=("signal",),
            features=np.arange(100, dtype=np.float64).reshape(-1, 1),
            targets=np.asarray(([-1, 0, 1, 0] * 25), dtype=np.int64),
        ),
        forward_returns=tuple(0.001 * (index % 3 - 1) for index in range(100)),
    )

    model_training, threshold_validation = _model_training_partitions(labeled)

    assert model_training.dataset.timestamps == timestamps[:64]
    assert threshold_validation.dataset.timestamps == timestamps[64:80]
    assert set(model_training.dataset.timestamps).isdisjoint(
        threshold_validation.dataset.timestamps
    )
    assert set(threshold_validation.dataset.timestamps).isdisjoint(timestamps[80:])


def test_training_partitions_purge_overlapping_label_horizons() -> None:
    start = datetime(2026, 8, 24, 10, tzinfo=UTC)
    timestamps = tuple(start + timedelta(minutes=index) for index in range(100))
    labeled = LabeledDataset(
        dataset=ChronologicalDataset(
            timestamps=timestamps,
            feature_names=("signal",),
            features=np.arange(100, dtype=np.float64).reshape(-1, 1),
            targets=np.asarray(([-1, 0, 1, 0] * 25), dtype=np.int64),
        ),
        forward_returns=tuple(0.001 * (index % 3 - 1) for index in range(100)),
    )

    model_training, threshold_validation = _model_training_partitions(
        labeled, embargo=timedelta(minutes=5)
    )

    assert model_training.dataset.timestamps[-1] == timestamps[54]
    assert threshold_validation.dataset.timestamps[0] == timestamps[60]
    assert (
        model_training.dataset.timestamps[-1] + timedelta(minutes=5)
        < (threshold_validation.dataset.timestamps[0])
    )


def test_strategy_metrics_describe_candidate_actions_and_recent_trades() -> None:
    start = datetime(2026, 8, 24, 10, tzinfo=UTC)
    timestamps = tuple(start + timedelta(minutes=15 * index) for index in range(4))
    labeled = LabeledDataset(
        dataset=ChronologicalDataset(
            timestamps=timestamps,
            feature_names=("signal",),
            features=np.asarray([[1.0], [2.0], [3.0], [4.0]], dtype=np.float64),
            targets=np.asarray([1, -1, 0, 1], dtype=np.int64),
        ),
        forward_returns=(0.02, -0.01, 0.03, -0.005),
    )
    target = TrainingTarget(
        instrument_id=uuid4(),
        horizon="15m",
        horizon_duration=timedelta(minutes=15),
    )

    metrics = _strategy_metrics(
        cast(EnsembleModel, PredictableModel((0.8, 0.2, 0.6, 0.9))),
        labeled,
        target,
    )

    assert metrics.trades == 3
    assert metrics.long_trades == 2
    assert metrics.short_trades == 1
    assert metrics.winning_trades == 2
    assert metrics.losing_trades == 1
    assert metrics.breakeven_trades == 0
    assert metrics.win_rate == 2 / 3
    assert metrics.gross_profit == pytest.approx(0.027)
    assert metrics.gross_loss == pytest.approx(0.0065)
    assert metrics.total_costs == pytest.approx(0.0045)
    assert metrics.recent_return == pytest.approx(-0.0065)
    assert tuple(item.direction for item in metrics.recent_trades) == ("LONG", "SHORT", "LONG")
    assert metrics.recent_trades[-1].occurred_at == timestamps[-1]


def test_threshold_is_selected_on_validation_return_with_required_trade_coverage() -> None:
    start = datetime(2026, 8, 24, 10, tzinfo=UTC)
    validation = LabeledDataset(
        dataset=ChronologicalDataset(
            timestamps=tuple(start + timedelta(minutes=15 * index) for index in range(4)),
            feature_names=("signal",),
            features=np.asarray([[0.55], [0.60], [0.70], [0.80]], dtype=np.float64),
            targets=np.asarray([1, 1, 1, 1], dtype=np.int64),
        ),
        forward_returns=(-0.10, -0.10, 0.02, 0.02),
    )
    target = TrainingTarget(uuid4(), "15m", timedelta(minutes=15))
    criteria = AdmissionCriteria(
        min_actionable_labels=1,
        min_closed_trades=2,
        min_closed_trade_fraction=0.5,
    )

    threshold = _select_confidence_threshold(
        cast(EnsembleModel, FeatureProbabilityModel()), validation, target, criteria
    )

    assert threshold == pytest.approx(0.70)
