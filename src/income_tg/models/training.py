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
        if set(np.unique(self.targets)) != {0, 1}:
            raise ValueError("Бинарная цель должна содержать оба класса 0 и 1")


def train_ensemble(
    dataset: ChronologicalDataset,
    *,
    calibration_fraction: float = 0.2,
    random_state: int = 42,
) -> EnsembleModel:
    dataset.validate()
    if not 0.1 <= calibration_fraction <= 0.4:
        raise ValueError("calibration_fraction должна находиться в диапазоне 0.1..0.4")
    split = int(len(dataset.features) * (1 - calibration_fraction))
    train_x = dataset.features[:split]
    train_y = dataset.targets[:split]
    calibration_x = dataset.features[split:]
    calibration_y = dataset.targets[split:]
    if len(np.unique(train_y)) != 2:
        raise ValueError("Обучающая часть должна содержать оба класса")

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
        logistic.predict_proba(scaler.transform(calibration_x))[:, 1]
        + forest.predict_proba(calibration_x)[:, 1]
    ) / 2
    calibrator: LogisticRegression | None = None
    if len(np.unique(calibration_y)) == 2:
        clipped = np.clip(raw_probabilities, 1e-6, 1 - 1e-6)
        logits = np.log(clipped / (1 - clipped)).reshape(-1, 1)
        calibrator = LogisticRegression(random_state=random_state).fit(logits, calibration_y)

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
            "calibration": "platt" if calibrator is not None else "identity",
            "random_state": random_state,
        },
    )
