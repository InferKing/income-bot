from __future__ import annotations

import math
from collections.abc import Sequence


def technical_features(
    closes: Sequence[float], highs: Sequence[float], lows: Sequence[float], volumes: Sequence[float]
) -> dict[str, float]:
    lengths = {len(closes), len(highs), len(lows), len(volumes)}
    if len(lengths) != 1 or len(closes) < 20:
        raise ValueError("Для технических признаков нужны минимум 20 синхронных свечей")
    if any(value <= 0 or not math.isfinite(value) for value in closes):
        raise ValueError("Цены закрытия должны быть положительными конечными числами")

    returns_1 = closes[-1] / closes[-2] - 1
    returns_5 = closes[-1] / closes[-6] - 1
    returns_15 = closes[-1] / closes[-16] - 1
    log_returns = [math.log(closes[index] / closes[index - 1]) for index in range(1, len(closes))]
    recent_returns = log_returns[-20:]
    mean_return = sum(recent_returns) / len(recent_returns)
    variance = sum((value - mean_return) ** 2 for value in recent_returns) / max(
        len(recent_returns) - 1, 1
    )
    volatility_20 = math.sqrt(variance)
    sma_5 = sum(closes[-5:]) / 5
    sma_20 = sum(closes[-20:]) / 20
    true_ranges = [
        max(
            highs[index] - lows[index],
            abs(highs[index] - closes[index - 1]),
            abs(lows[index] - closes[index - 1]),
        )
        for index in range(len(closes) - 14, len(closes))
    ]
    atr_14 = sum(true_ranges) / len(true_ranges)
    volume_mean = sum(volumes[-20:]) / 20
    volume_ratio = volumes[-1] / volume_mean if volume_mean > 0 else 0.0
    return {
        "return_1": returns_1,
        "return_5": returns_5,
        "return_15": returns_15,
        "volatility_20": volatility_20,
        "sma_ratio_5_20": sma_5 / sma_20 - 1,
        "close_to_sma_20": closes[-1] / sma_20 - 1,
        "atr_fraction_14": atr_14 / closes[-1],
        "volume_ratio_20": volume_ratio,
    }
