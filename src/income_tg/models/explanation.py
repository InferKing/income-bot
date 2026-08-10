from __future__ import annotations

FEATURE_LABELS = {
    "return_1": "краткосрочное изменение цены",
    "return_5": "пятиминутный импульс",
    "return_15": "среднесрочный импульс",
    "volatility_20": "текущая волатильность",
    "sma_ratio_5_20": "соотношение короткого и длинного тренда",
    "orderbook_imbalance_10": "дисбаланс стакана",
    "trade_flow_imbalance": "перевес агрессивных покупок и продаж",
    "funding_rate": "ставка финансирования",
    "open_interest_change": "изменение открытого интереса",
    "basis_bps": "базис фьючерса",
    "liquidation_imbalance": "дисбаланс ликвидаций",
}


def explain_contributions(
    contributions: tuple[tuple[str, float], ...], *, limit: int = 5
) -> tuple[str, ...]:
    if limit <= 0:
        raise ValueError("limit должен быть положительным")
    result: list[str] = []
    for feature, value in contributions[:limit]:
        label = FEATURE_LABELS.get(feature, feature)
        direction = "поддерживает рост" if value > 0 else "поддерживает снижение"
        result.append(f"{label}: {direction}")
    return tuple(result)
