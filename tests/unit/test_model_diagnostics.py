from datetime import UTC, datetime, timedelta

import numpy as np

from income_tg.models.dataset import LabeledDataset
from income_tg.models.diagnostics import audit_features
from income_tg.models.training import ChronologicalDataset


def test_feature_audit_reports_constants_correlation_and_drift() -> None:
    start = datetime(2026, 8, 24, tzinfo=UTC)
    trend = np.arange(100, dtype=np.float64)
    constant = np.zeros(100, dtype=np.float64)
    labeled = LabeledDataset(
        dataset=ChronologicalDataset(
            timestamps=tuple(start + timedelta(minutes=index) for index in range(100)),
            feature_names=("trend", "constant"),
            features=np.column_stack((trend, constant)),
            targets=np.asarray(([-1, 0, 1, 0] * 25), dtype=np.int64),
        ),
        forward_returns=tuple(float(value) for value in trend),
    )

    trend_audit, constant_audit = audit_features(labeled, embargo=timedelta(minutes=5))

    assert trend_audit.return_correlation > 0.99
    assert trend_audit.drift_score > 1
    assert not trend_audit.is_constant
    assert constant_audit.is_constant
    assert constant_audit.zero_fraction == 1
