from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.models.training import ChronologicalDataset
from income_tg.storage.trading_models import FeatureVectorRecord, MarketCandleRecord


@dataclass(frozen=True, slots=True)
class LabeledDataset:
    dataset: ChronologicalDataset
    forward_returns: tuple[float, ...]


async def load_labeled_dataset(
    session: AsyncSession,
    *,
    instrument_id: UUID,
    horizon: str,
    horizon_duration: timedelta,
    minimum_actionable_return: float = 0.0,
    candle_provider: str = "bybit",
) -> LabeledDataset:
    if not math.isfinite(minimum_actionable_return) or minimum_actionable_return < 0:
        raise ValueError("minimum_actionable_return must be finite and non-negative")
    if not candle_provider.strip():
        raise ValueError("candle_provider must not be empty")
    vectors = list(
        await session.scalars(
            select(FeatureVectorRecord)
            .where(
                FeatureVectorRecord.instrument_id == instrument_id,
                FeatureVectorRecord.horizon == horizon,
            )
            .order_by(FeatureVectorRecord.as_of)
        )
    )
    candles = list(
        await session.scalars(
            select(MarketCandleRecord)
            .where(
                MarketCandleRecord.instrument_id == instrument_id,
                MarketCandleRecord.provider == candle_provider,
                MarketCandleRecord.is_closed.is_(True),
            )
            .order_by(MarketCandleRecord.opened_at)
        )
    )
    if not vectors or not candles:
        raise ValueError("Недостаточно сохраненных признаков или свечей")
    close_times = [
        candle.opened_at + timedelta(seconds=candle.interval_seconds) for candle in candles
    ]
    feature_names = tuple(vectors[0].names)
    timestamps = []
    rows: list[list[float]] = []
    targets: list[int] = []
    forward_returns: list[float] = []
    for vector in vectors:
        if tuple(vector.names) != feature_names or vector.data_cutoff > vector.as_of:
            continue
        current_index = bisect_right(close_times, vector.as_of) - 1
        future_index = bisect_left(close_times, vector.as_of + horizon_duration)
        if current_index < 0 or future_index >= len(candles):
            continue
        current = float(candles[current_index].close)
        future = float(candles[future_index].close)
        forward_return = future / current - 1
        timestamps.append(vector.as_of)
        rows.append([float(value) for value in vector.values])
        targets.append(classify_forward_return(forward_return, minimum_actionable_return))
        forward_returns.append(forward_return)
    if len(rows) < 40:
        raise ValueError("Для обучения требуется минимум 40 размеченных векторов")
    return LabeledDataset(
        dataset=ChronologicalDataset(
            timestamps=tuple(timestamps),
            feature_names=feature_names,
            features=np.asarray(rows, dtype=np.float64),
            targets=np.asarray(targets, dtype=np.int64),
        ),
        forward_returns=tuple(forward_returns),
    )


def classify_forward_return(forward_return: float, minimum_actionable_return: float) -> int:
    if forward_return > minimum_actionable_return:
        return 1
    if forward_return < -minimum_actionable_return:
        return -1
    return 0


def chronological_train_test(
    labeled: LabeledDataset, test_fraction: float = 0.2
) -> tuple[LabeledDataset, LabeledDataset]:
    if not 0.1 <= test_fraction <= 0.4:
        raise ValueError("test_fraction должна находиться в диапазоне 0.1..0.4")
    split = int(len(labeled.dataset.timestamps) * (1 - test_fraction))
    return _slice(labeled, 0, split), _slice(labeled, split, len(labeled.dataset.timestamps))


def chronological_windows(labeled: LabeledDataset, window_count: int) -> tuple[LabeledDataset, ...]:
    if window_count <= 0:
        raise ValueError("window_count must be positive")
    samples = len(labeled.dataset.timestamps)
    if samples < window_count:
        raise ValueError("Для временных окон недостаточно размеченных векторов")
    boundaries = [samples * index // window_count for index in range(window_count + 1)]
    return tuple(
        _slice(labeled, boundaries[index], boundaries[index + 1]) for index in range(window_count)
    )


def _slice(labeled: LabeledDataset, start: int, end: int) -> LabeledDataset:
    dataset = labeled.dataset
    return LabeledDataset(
        dataset=ChronologicalDataset(
            timestamps=dataset.timestamps[start:end],
            feature_names=dataset.feature_names,
            features=dataset.features[start:end],
            targets=dataset.targets[start:end],
        ),
        forward_returns=labeled.forward_returns[start:end],
    )
