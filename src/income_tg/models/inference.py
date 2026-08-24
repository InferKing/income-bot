from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
from numpy.typing import NDArray
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler


@dataclass(frozen=True, slots=True)
class ModelPrediction:
    as_of: datetime
    probability_up: float
    probability_down: float
    probability_no_trade: float
    confidence: float
    expected_directional_score: float
    contributions: tuple[tuple[str, float], ...]
    model_version: str


@dataclass(slots=True)
class EnsembleModel:
    feature_names: tuple[str, ...]
    scaler: StandardScaler
    logistic: LogisticRegression
    forest: RandomForestClassifier
    calibrator: LogisticRegression | None
    version: str
    trained_at: datetime
    metadata: dict[str, Any]

    @property
    def confidence_threshold(self) -> float:
        value = self.metadata.get("confidence_threshold", 0.70)
        return float(value) if isinstance(value, int | float) else 0.70

    def predict(
        self,
        *,
        as_of: datetime,
        feature_names: tuple[str, ...],
        values: tuple[float, ...],
    ) -> ModelPrediction:
        if feature_names != self.feature_names:
            raise ValueError("Схема признаков не совпадает со схемой модели")
        array = np.asarray([values], dtype=np.float64)
        if not np.isfinite(array).all():
            raise ValueError("Признаки содержат NaN или бесконечность")
        scaled = self.scaler.transform(array)
        raw_probabilities = (
            self.logistic.predict_proba(scaled)[0] + self.forest.predict_proba(array)[0]
        ) / 2
        calibrated = self._calibrate(raw_probabilities)
        probabilities = {
            int(label): float(probability)
            for label, probability in zip(self.logistic.classes_, calibrated, strict=True)
        }
        probability_down = probabilities.get(-1, 0.0)
        probability_no_trade = probabilities.get(0, 0.0)
        probability_up = probabilities.get(1, 0.0)
        contributions = self._contributions(array, scaled)
        return ModelPrediction(
            as_of=as_of,
            probability_up=probability_up,
            probability_down=probability_down,
            probability_no_trade=probability_no_trade,
            confidence=max(probability_up, probability_down),
            expected_directional_score=probability_up - probability_down,
            contributions=contributions,
            model_version=self.version,
        )

    def _calibrate(self, probabilities: NDArray[np.float64]) -> NDArray[np.float64]:
        clipped = np.clip(probabilities, 1e-6, 1.0)
        clipped = clipped / clipped.sum()
        if self.calibrator is None:
            return clipped
        calibrated = self.calibrator.predict_proba(np.log(clipped).reshape(1, -1))[0]
        return np.asarray(np.clip(calibrated, 0.0, 1.0), dtype=np.float64)

    def _contributions(
        self, raw: NDArray[np.float64], scaled: NDArray[np.float64]
    ) -> tuple[tuple[str, float], ...]:
        class_indexes = {int(label): index for index, label in enumerate(self.logistic.classes_)}
        directional_coefficients = (
            self.logistic.coef_[class_indexes[1]] - self.logistic.coef_[class_indexes[-1]]
        )
        logistic_values = scaled[0] * directional_coefficients
        forest_importance = self.forest.feature_importances_
        combined = logistic_values + np.sign(logistic_values) * forest_importance
        pairs = list(zip(self.feature_names, (float(value) for value in combined), strict=True))
        pairs.sort(key=lambda item: abs(item[1]), reverse=True)
        return tuple(pairs)
