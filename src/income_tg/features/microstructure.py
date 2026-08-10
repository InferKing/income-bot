from __future__ import annotations

import math
from collections.abc import Sequence


def microstructure_features(
    bids: Sequence[tuple[float, float]],
    asks: Sequence[tuple[float, float]],
    aggressive_buy_volume: float,
    aggressive_sell_volume: float,
) -> dict[str, float]:
    if not bids or not asks:
        raise ValueError("Для микроструктурных признаков нужен непустой стакан")
    best_bid, best_ask = bids[0][0], asks[0][0]
    if best_bid <= 0 or best_ask <= best_bid:
        raise ValueError("Некорректные лучшие цены стакана")
    if any(price <= 0 or quantity < 0 for price, quantity in [*bids, *asks]):
        raise ValueError("Некорректный уровень стакана")

    midpoint = (best_bid + best_ask) / 2
    bid_depth = sum(quantity for _, quantity in bids[:10])
    ask_depth = sum(quantity for _, quantity in asks[:10])
    depth_total = bid_depth + ask_depth
    flow_total = aggressive_buy_volume + aggressive_sell_volume
    if not all(
        math.isfinite(value) and value >= 0
        for value in (aggressive_buy_volume, aggressive_sell_volume)
    ):
        raise ValueError("Объемы потока сделок должны быть конечными и неотрицательными")
    return {
        "spread_bps": (best_ask - best_bid) / midpoint * 10_000,
        "orderbook_imbalance_10": (bid_depth - ask_depth) / depth_total if depth_total else 0.0,
        "trade_flow_imbalance": (
            (aggressive_buy_volume - aggressive_sell_volume) / flow_total if flow_total else 0.0
        ),
        "top_bid_depth": bid_depth,
        "top_ask_depth": ask_depth,
    }
