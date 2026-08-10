from __future__ import annotations

import math
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
        raw_probability = float(
            (self.logistic.predict_proba(scaled)[0, 1] + self.forest.predict_proba(array)[0, 1]) / 2
        )
        probability_up = self._calibrate(raw_probability)
        contributions = self._contributions(array, scaled)
        return ModelPrediction(
            as_of=as_of,
            probability_up=probability_up,
            probability_down=1 - probability_up,
            confidence=max(probability_up, 1 - probability_up),
            expected_directional_score=probability_up - 0.5,
            contributions=contributions,
            model_version=self.version,
        )

    def _calibrate(self, probability: float) -> float:
        clipped = min(max(probability, 1e-6), 1 - 1e-6)
        if self.calibrator is None:
            return clipped
        logit = math.log(clipped / (1 - clipped))
        calibrated = float(self.calibrator.predict_proba(np.asarray([[logit]]))[0, 1])
        return min(max(calibrated, 0.0), 1.0)

    def _contributions(
        self, raw: NDArray[np.float64], scaled: NDArray[np.float64]
    ) -> tuple[tuple[str, float], ...]:
        logistic_values = scaled[0] * self.logistic.coef_[0]
        forest_importance = self.forest.feature_importances_
        combined = logistic_values + np.sign(logistic_values) * forest_importance
        pairs = list(zip(self.feature_names, (float(value) for value in combined), strict=True))
        pairs.sort(key=lambda item: abs(item[1]), reverse=True)
        return tuple(pairs)
