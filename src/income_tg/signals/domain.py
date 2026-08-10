from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class MarketType(StrEnum):
    SPOT = "SPOT"
    LINEAR_PERPETUAL = "LINEAR_PERPETUAL"


class SignalAction(StrEnum):
    BUY = "BUY"
    LONG = "LONG"
    SHORT = "SHORT"
    CLOSE = "CLOSE"
    HOLD = "HOLD"


class PositionDirection(StrEnum):
    SPOT = "SPOT"
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True, slots=True)
class SignalCandidate:
    instrument: str
    market_type: MarketType
    action: SignalAction
    confidence: float
    reference_price: float
    horizon: str
    as_of: datetime
    valid_until: datetime
    model_version: str
    reasons: tuple[str, ...]
    cancel_condition: str


@dataclass(frozen=True, slots=True)
class ActivePosition:
    direction: PositionDirection
    stop_loss: float | None = None
    liquidation_price: float | None = None
