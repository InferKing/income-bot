from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from income_tg.features.pipeline import CandleInput, FeatureVector, MarketObservation
from income_tg.models.inference import ModelPrediction
from income_tg.paper_trading.engine import PaperExecutionEngine
from income_tg.paper_trading.models import (
    InstrumentKind,
    MarketSnapshot,
    PaperPosition,
    PositionSide,
)
from income_tg.paper_trading.repository import PaperPersistenceResult
from income_tg.risk.engine import RiskEngine
from income_tg.risk.models import PortfolioRiskState, RiskLimits
from income_tg.signals.domain import MarketType
from income_tg.signals.policy import SignalPolicy
from income_tg.worker.engine import TradingWorker
from income_tg.worker.models import TradingWorkItem, WorkerStatus

NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)


class FakeFeatures:
    def build(self, observation: MarketObservation) -> FeatureVector:
        return FeatureVector(
            observation.instrument, observation.as_of, observation.data_cutoff, ("x",), (1.0,)
        )


class FakeModel:
    def __init__(self, probability_up: float) -> None:
        self.probability_up = probability_up

    def predict(
        self, *, as_of: datetime, feature_names: tuple[str, ...], values: tuple[float, ...]
    ) -> ModelPrediction:
        del feature_names, values
        up = self.probability_up
        return ModelPrediction(
            as_of, up, 1 - up, max(up, 1 - up), up - 0.5, (("x", 1.0),), "champion-1"
        )


@dataclass
class FakeSignal:
    id: UUID


class FakeSignals:
    def __init__(self) -> None:
        self.calls = 0

    async def record(self, **kwargs: Any) -> FakeSignal:
        del kwargs
        self.calls += 1
        return FakeSignal(uuid4())


class FakePaper:
    def __init__(self, duplicate: bool = False) -> None:
        self.duplicate = duplicate
        self.opens = 0

    async def has_order(self, idempotency_key: str) -> bool:
        del idempotency_key
        return self.duplicate

    async def reserve_order(self, **kwargs: Any) -> PaperPersistenceResult:
        del kwargs
        return PaperPersistenceResult(uuid4(), not self.duplicate)

    async def record_open(self, **kwargs: Any) -> PaperPersistenceResult:
        del kwargs
        self.opens += 1
        return PaperPersistenceResult(uuid4(), True)

    async def record_close(self, **kwargs: Any) -> PaperPersistenceResult:
        del kwargs
        return PaperPersistenceResult(uuid4(), True)


def item(*, cutoff: datetime = NOW) -> TradingWorkItem:
    observation = MarketObservation(
        instrument="BTCUSDT",
        as_of=cutoff,
        data_cutoff=cutoff,
        candles=(CandleInput(cutoff, 100, 101, 99, 10),),
        bids=((99.9, 1.0),),
        asks=((100.1, 1.0),),
        aggressive_buy_volume=1,
        aggressive_sell_volume=1,
        orderbook_at=cutoff,
        trade_flow_at=cutoff,
        derivatives_at=cutoff,
    )
    return TradingWorkItem(
        user_id=uuid4(),
        portfolio_id=uuid4(),
        instrument_id=uuid4(),
        observation=observation,
        market=MarketSnapshot(
            cutoff, Decimal("99.9"), Decimal("100.1"), Decimal("99"), Decimal("101")
        ),
        market_type=MarketType.LINEAR_PERPETUAL,
        portfolio=PortfolioRiskState(
            Decimal("100000"), Decimal("100000"), Decimal("100000"), Decimal("100000"), 0
        ),
        base_asset="BTC",
        quote_asset="USDT",
    )


def worker(
    probability: float, paper: FakePaper | None = None, *, max_positions: int = 3
) -> tuple[TradingWorker, FakeSignals, FakePaper]:
    signals = FakeSignals()
    store = paper or FakePaper()
    result = TradingWorker(
        feature_pipeline=FakeFeatures(),
        active_model=FakeModel(probability),
        signal_policy=SignalPolicy(),
        risk_engine=RiskEngine(
            RiskLimits(max_open_positions=max_positions, max_market_age=timedelta(minutes=1))
        ),
        signal_service=signals,  # type: ignore[arg-type]
        execution_engine=PaperExecutionEngine(),
        paper_repository=store,  # type: ignore[arg-type]
    )
    return result, signals, store


@pytest.mark.asyncio
async def test_approved_signal_executes_with_single_stop_and_take() -> None:
    service, signals, paper = worker(0.8)
    outcome = await service.process(item(), now=NOW)

    assert outcome.status is WorkerStatus.EXECUTED
    assert outcome.stop_loss == Decimal("98.000")
    assert outcome.take_profit == Decimal("104.000")
    assert signals.calls == 1
    assert paper.opens == 1


@pytest.mark.asyncio
async def test_hold_has_no_side_effects() -> None:
    service, signals, paper = worker(0.55)
    outcome = await service.process(item(), now=NOW)
    assert outcome.status is WorkerStatus.HOLD
    assert signals.calls == paper.opens == 0


@pytest.mark.asyncio
async def test_stale_observation_stops_before_model_side_effects() -> None:
    service, signals, paper = worker(0.8)
    outcome = await service.process(item(cutoff=NOW - timedelta(minutes=3)), now=NOW)
    assert outcome.status is WorkerStatus.STALE
    assert signals.calls == paper.opens == 0


@pytest.mark.asyncio
async def test_duplicate_stops_before_pipeline() -> None:
    service, signals, paper = worker(0.8, FakePaper(duplicate=True))
    outcome = await service.process(item(), now=NOW)
    assert outcome.status is WorkerStatus.DUPLICATE
    assert signals.calls == paper.opens == 0


@pytest.mark.asyncio
async def test_risk_rejection_does_not_execute() -> None:
    service, signals, paper = worker(0.8, max_positions=1)
    work = item()
    rejected = replace(
        work,
        portfolio=PortfolioRiskState(
            Decimal("100000"),
            Decimal("100000"),
            Decimal("100000"),
            Decimal("100000"),
            1,
        ),
    )
    outcome = await service.process(rejected, now=NOW)
    assert outcome.status is WorkerStatus.RISK_REJECTED
    assert signals.calls == paper.opens == 0


@pytest.mark.asyncio
async def test_expired_signal_is_not_recorded_or_executed() -> None:
    signals = FakeSignals()
    paper = FakePaper()
    service = TradingWorker(
        feature_pipeline=FakeFeatures(),
        active_model=FakeModel(0.8),
        signal_policy=SignalPolicy(),
        risk_engine=RiskEngine(),
        signal_service=signals,  # type: ignore[arg-type]
        execution_engine=PaperExecutionEngine(),
        paper_repository=paper,  # type: ignore[arg-type]
        maximum_observation_age=timedelta(hours=1),
    )

    outcome = await service.process(item(), now=NOW + timedelta(minutes=16))

    assert outcome.status is WorkerStatus.SIGNAL_EXPIRED
    assert signals.calls == paper.opens == 0


@pytest.mark.asyncio
async def test_cross_symbol_active_position_is_rejected_before_close() -> None:
    service, signals, paper = worker(0.1)
    work = item()
    wrong_position = PaperPosition(
        position_id="eth-position",
        symbol="ETHUSDT",
        instrument=InstrumentKind.PERPETUAL,
        side=PositionSide.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        leverage=2,
        margin=Decimal("50"),
        stop_loss=Decimal("98"),
        take_profit=Decimal("104"),
        opening_commission=Decimal("0.01"),
        funding_pnl=Decimal("0"),
        opened_at=NOW,
        liquidation_price=Decimal("50"),
    )

    outcome = await service.process(replace(work, active_position=wrong_position), now=NOW)

    assert outcome.status is WorkerStatus.INVALID_OBSERVATION
    assert signals.calls == paper.opens == 0


def test_business_fingerprint_ignores_market_quote_noise() -> None:
    work = item()
    moved_market = replace(
        work,
        market=MarketSnapshot(
            NOW,
            Decimal("100"),
            Decimal("100.2"),
            Decimal("99"),
            Decimal("101"),
        ),
    )

    assert TradingWorker.business_fingerprint(work) == TradingWorker.business_fingerprint(
        moved_market
    )
