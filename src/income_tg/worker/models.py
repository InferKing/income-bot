from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from income_tg.features.pipeline import MarketObservation
from income_tg.paper_trading.models import MarketSnapshot, PaperPosition
from income_tg.risk.models import PortfolioRiskState, RiskDecision
from income_tg.signals.domain import MarketType, SignalCandidate


class WorkerStatus(StrEnum):
    STALE = "STALE"
    INVALID_OBSERVATION = "INVALID_OBSERVATION"
    HOLD = "HOLD"
    SIGNAL_EXPIRED = "SIGNAL_EXPIRED"
    RISK_REJECTED = "RISK_REJECTED"
    EXECUTION_REJECTED = "EXECUTION_REJECTED"
    EXECUTED = "EXECUTED"
    DUPLICATE = "DUPLICATE"


@dataclass(frozen=True, slots=True)
class TradingWorkItem:
    user_id: UUID
    portfolio_id: UUID
    instrument_id: UUID
    observation: MarketObservation
    market: MarketSnapshot
    market_type: MarketType
    portfolio: PortfolioRiskState
    base_asset: str
    quote_asset: str
    horizon: str = "1h"
    active_position: PaperPosition | None = None

    def __post_init__(self) -> None:
        base = self.base_asset.strip().upper()
        quote = self.quote_asset.strip().upper()
        if not base or not quote:
            raise ValueError("base_asset and quote_asset are required")
        if not base.isalnum() or not quote.isalnum():
            raise ValueError("base_asset and quote_asset must be alphanumeric")
        if base == quote:
            raise ValueError("base_asset and quote_asset must differ")
        if not self.horizon.strip():
            raise ValueError("horizon is required")
        object.__setattr__(self, "base_asset", base)
        object.__setattr__(self, "quote_asset", quote)
        object.__setattr__(self, "horizon", self.horizon.strip())


@dataclass(frozen=True, slots=True)
class WorkerOutcome:
    status: WorkerStatus
    idempotency_key: str
    candidate: SignalCandidate | None = None
    risk_decision: RiskDecision | None = None
    signal_id: UUID | None = None
    paper_order_id: UUID | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    detail: str | None = None
    processed_at: datetime | None = None
