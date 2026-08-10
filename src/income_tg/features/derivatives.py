from __future__ import annotations

import math


def derivatives_features(
    *,
    funding_rate: float,
    open_interest: float,
    previous_open_interest: float,
    mark_price: float,
    index_price: float,
    long_liquidations: float,
    short_liquidations: float,
) -> dict[str, float]:
    values = (
        funding_rate,
        open_interest,
        previous_open_interest,
        mark_price,
        index_price,
        long_liquidations,
        short_liquidations,
    )
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Деривативные показатели должны быть конечными")
    if (
        min(
            open_interest,
            previous_open_interest,
            mark_price,
            index_price,
            long_liquidations,
            short_liquidations,
        )
        < 0
    ):
        raise ValueError("Деривативные показатели не могут быть отрицательными")
    oi_change = open_interest / previous_open_interest - 1 if previous_open_interest > 0 else 0.0
    basis_bps = (mark_price / index_price - 1) * 10_000 if index_price > 0 else 0.0
    liquidations_total = long_liquidations + short_liquidations
    return {
        "funding_rate": funding_rate,
        "open_interest_change": oi_change,
        "basis_bps": basis_bps,
        "liquidation_imbalance": (
            (short_liquidations - long_liquidations) / liquidations_total
            if liquidations_total
            else 0.0
        ),
    }
