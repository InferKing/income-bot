from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta

import numpy as np

from income_tg.models.dataset import LabeledDataset, chronological_train_test


@dataclass(frozen=True, slots=True)
class FeatureAudit:
    name: str
    samples: int
    finite_fraction: float
    unique_values: int
    dominant_fraction: float
    zero_fraction: float
    standard_deviation: float
    return_correlation: float
    drift_score: float
    is_constant: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def audit_features(
    labeled: LabeledDataset,
    *,
    embargo: timedelta | None = None,
) -> tuple[FeatureAudit, ...]:
    training, test = chronological_train_test(labeled, embargo=embargo)
    returns = np.asarray(labeled.forward_returns, dtype=np.float64)
    audits: list[FeatureAudit] = []
    for index, name in enumerate(labeled.dataset.feature_names):
        values = np.asarray(labeled.dataset.features[:, index], dtype=np.float64)
        finite = np.isfinite(values)
        finite_values = values[finite]
        unique, counts = np.unique(finite_values, return_counts=True)
        standard_deviation = _safe_std(finite_values)
        correlation = _safe_correlation(finite_values, returns[finite])
        train_values = np.asarray(training.dataset.features[:, index], dtype=np.float64)
        test_values = np.asarray(test.dataset.features[:, index], dtype=np.float64)
        drift = _standardized_drift(train_values, test_values)
        audits.append(
            FeatureAudit(
                name=name,
                samples=len(values),
                finite_fraction=float(finite.mean()) if len(finite) else 0.0,
                unique_values=len(unique),
                dominant_fraction=(
                    float(counts.max() / len(finite_values)) if len(counts) else 1.0
                ),
                zero_fraction=(
                    float(np.count_nonzero(finite_values == 0) / len(finite_values))
                    if len(finite_values)
                    else 1.0
                ),
                standard_deviation=standard_deviation,
                return_correlation=correlation,
                drift_score=drift,
                is_constant=len(unique) <= 1 or standard_deviation <= 1e-12,
            )
        )
    return tuple(audits)


def _safe_std(values: np.ndarray) -> float:
    return float(np.std(values)) if len(values) else 0.0


def _safe_correlation(left: np.ndarray, right: np.ndarray) -> float:
    if len(left) < 2 or _safe_std(left) <= 1e-12 or _safe_std(right) <= 1e-12:
        return 0.0
    value = float(np.corrcoef(left, right)[0, 1])
    return value if np.isfinite(value) else 0.0


def _standardized_drift(training: np.ndarray, test: np.ndarray) -> float:
    training = training[np.isfinite(training)]
    test = test[np.isfinite(test)]
    if not len(training) or not len(test):
        return 0.0
    scale = max(_safe_std(training), 1e-12)
    return float(abs(np.mean(test) - np.mean(training)) / scale)
