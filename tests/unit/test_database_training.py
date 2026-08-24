from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import numpy as np
import pytest

from income_tg.jobs.database_training import TrainingTarget, _strategy_metrics
from income_tg.models.dataset import LabeledDataset
from income_tg.models.inference import EnsembleModel, ModelPrediction
from income_tg.models.training import ChronologicalDataset


class PredictableModel:
    feature_names = ("signal",)

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
            confidence=max(probability_up, 1 - probability_up),
            expected_directional_score=probability_up - 0.5,
            contributions=(),
            model_version="candidate",
        )


def test_strategy_metrics_describe_candidate_actions_and_recent_trades() -> None:
    start = datetime(2026, 8, 24, 10, tzinfo=UTC)
    timestamps = tuple(start + timedelta(minutes=15 * index) for index in range(4))
    labeled = LabeledDataset(
        dataset=ChronologicalDataset(
            timestamps=timestamps,
            feature_names=("signal",),
            features=np.asarray([[1.0], [2.0], [3.0], [4.0]], dtype=np.float64),
            targets=np.asarray([1, 0, 1, 0], dtype=np.int64),
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
