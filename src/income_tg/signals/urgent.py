from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from income_tg.signals.domain import ActivePosition, PositionDirection


class UrgentEventType(StrEnum):
    STOP_APPROACHING = "STOP_APPROACHING"
    LIQUIDATION_APPROACHING = "LIQUIDATION_APPROACHING"
    VOLATILITY_SPIKE = "VOLATILITY_SPIKE"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    DRAWDOWN_LIMIT = "DRAWDOWN_LIMIT"
    STALE_DATA = "STALE_DATA"


@dataclass(frozen=True, slots=True)
class UrgentEvent:
    event_type: UrgentEventType
    message: str


def position_alerts(
    position: ActivePosition,
    current_price: float,
    *,
    warning_distance: float = 0.01,
) -> tuple[UrgentEvent, ...]:
    if current_price <= 0 or not 0 < warning_distance < 1:
        raise ValueError("Некорректные параметры контроля позиции")
    events: list[UrgentEvent] = []
    for event_type, label, level in (
        (UrgentEventType.STOP_APPROACHING, "стоп-лоссу", position.stop_loss),
        (
            UrgentEventType.LIQUIDATION_APPROACHING,
            "расчетной ликвидации",
            position.liquidation_price,
        ),
    ):
        if level is None or level <= 0:
            continue
        distance = abs(current_price - level) / current_price
        adverse_side = (
            current_price >= level
            if position.direction in {PositionDirection.SPOT, PositionDirection.LONG}
            else current_price <= level
        )
        if adverse_side and distance <= warning_distance:
            events.append(
                UrgentEvent(
                    event_type=event_type,
                    message=f"Цена приблизилась к {label}: {level:.8g}",
                )
            )
    return tuple(events)
