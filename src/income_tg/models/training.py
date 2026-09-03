from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import numpy as np
from numpy.typing import NDArray
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from income_tg.models.inference import EnsembleModel


@dataclass(frozen=True, slots=True)
class ChronologicalDataset:
    timestamps: tuple[datetime, ...]
    feature_names: tuple[str, ...]
    features: NDArray[np.float64]
    targets: NDArray[np.int64]

    def validate(self) -> None:
        if len(self.timestamps) != len(self.features) or len(self.features) != len(self.targets):
            raise ValueError("Размеры timestamps, features и targets не совпадают")
        if len(self.timestamps) < 40:
            raise ValueError("Для обучения требуется минимум 40 наблюдений")
        if self.features.ndim != 2 or self.features.shape[1] != len(self.feature_names):
            raise ValueError("Некорректная размерность матрицы признаков")
        if not all(
            left < right for left, right in zip(self.timestamps, self.timestamps[1:], strict=False)
        ):
            raise ValueError("Наблюдения должны быть строго упорядочены по времени")
        if not np.isfinite(self.features).all():
            raise ValueError("Матрица признаков содержит NaN или бесконечность")
        if set(np.unique(self.targets)) != {-1, 0, 1}:
            raise ValueError("Цель должна содержать классы SHORT (-1), NO TRADE (0) и LONG (1)")


def train_ensemble(
    dataset: ChronologicalDataset,
    *,
    calibration_fraction: float = 0.2,
    target_action_fraction: float = 0.2,
    random_state: int = 42,
) -> EnsembleModel:
    dataset.validate()
    if not 0.1 <= calibration_fraction <= 0.4:
        raise ValueError("calibration_fraction должна находиться в диапазоне 0.1..0.4")
    if not 0 < target_action_fraction < 1:
        raise ValueError("target_action_fraction должна находиться между 0 и 1")
    split = int(len(dataset.features) * (1 - calibration_fraction))
    train_x = dataset.features[:split]
    train_y = dataset.targets[:split]
    calibration_x = dataset.features[split:]
    calibration_y = dataset.targets[split:]
    if set(np.unique(train_y)) != {-1, 0, 1}:
        raise ValueError("Обучающая часть должна содержать все три класса")

    scaler = StandardScaler().fit(train_x)
    logistic = LogisticRegression(
        max_iter=2_000,
        class_weight="balanced",
        random_state=random_state,
    ).fit(scaler.transform(train_x), train_y)
    forest = RandomForestClassifier(
        n_estimators=150,
        max_depth=6,
        min_samples_leaf=3,
        class_weight="balanced",
        random_state=random_state,
        n_jobs=1,
    ).fit(train_x, train_y)

    raw_probabilities = (
        logistic.predict_proba(scaler.transform(calibration_x))
        + forest.predict_proba(calibration_x)
    ) / 2
    calibrator: LogisticRegression | None = None
    if set(np.unique(calibration_y)) == {-1, 0, 1}:
        clipped = np.clip(raw_probabilities, 1e-6, 1 - 1e-6)
        calibrator = LogisticRegression(
            max_iter=2_000,
            class_weight="balanced",
            random_state=random_state,
        ).fit(np.log(clipped), calibration_y)

    calibrated = (
        raw_probabilities
        if calibrator is None
        else calibrator.predict_proba(np.log(np.clip(raw_probabilities, 1e-6, 1.0)))
    )
    class_indexes = {int(label): index for index, label in enumerate(logistic.classes_)}
    directional_confidence = np.maximum(
        calibrated[:, class_indexes[-1]], calibrated[:, class_indexes[1]]
    )
    actionable = directional_confidence[directional_confidence > calibrated[:, class_indexes[0]]]
    required_actions = int(np.ceil(len(calibration_x) * target_action_fraction))
    confidence_threshold = (
        float(np.sort(actionable)[-required_actions])
        if len(actionable) >= required_actions
        else float(min(actionable, default=1.0))
    )

    return EnsembleModel(
        feature_names=dataset.feature_names,
        scaler=scaler,
        logistic=logistic,
        forest=forest,
        calibrator=calibrator,
        version=f"ensemble-{uuid4().hex[:12]}",
        trained_at=datetime.now(UTC),
        metadata={
            "samples": len(dataset.features),
            "train_samples": split,
            "calibration_samples": len(dataset.features) - split,
            "calibration": "multinomial" if calibrator is not None else "identity",
            "calibration_class_weight": "balanced" if calibrator is not None else None,
            "calibration_actionable": len(actionable),
            "calibration_required_actions": required_actions,
            "target_action_fraction": target_action_fraction,
            "confidence_threshold": confidence_threshold,
            "random_state": random_state,
        },
    )
