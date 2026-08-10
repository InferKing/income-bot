"""End-to-end closed-observation trading worker."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from income_tg.features.pipeline import FeatureVector, MarketObservation
from income_tg.models.inference import ModelPrediction
from income_tg.paper_trading.models import (
    CloseResult,
    InstrumentKind,
    MarketSnapshot,
    OpenPositionResult,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    PaperPosition,
    PositionSide,
)
from income_tg.paper_trading.repository import PaperPersistenceResult
from income_tg.risk.models import (
    ExecutionCosts,
    MarketGuard,
    PositionDirection,
    RiskDecision,
    SizingRequest,
    VenueConstraints,
)
from income_tg.signals.domain import (
    ActivePosition,
    MarketType,
    SignalAction,
    SignalCandidate,
)
from income_tg.signals.domain import (
    PositionDirection as SignalPositionDirection,
)
from income_tg.worker.models import TradingWorkItem, WorkerOutcome, WorkerStatus


class FeatureBuilder(Protocol):
    def build(self, observation: MarketObservation) -> FeatureVector: ...


class PredictionModel(Protocol):
    def predict(
        self,
        *,
        as_of: datetime,
        feature_names: tuple[str, ...],
        values: tuple[float, ...],
    ) -> ModelPrediction: ...


class PredictionRecorder(Protocol):
    async def record(
        self,
        item: TradingWorkItem,
        prediction: ModelPrediction,
        vector: FeatureVector,
    ) -> UUID | None: ...


class CandidatePolicy(Protocol):
    def create_candidate(
        self,
        *,
        instrument: str,
        market_type: MarketType,
        reference_price: float,
        horizon: str,
        prediction: ModelPrediction,
        current_position: ActivePosition | None = None,
        validity: timedelta = timedelta(minutes=15),
    ) -> SignalCandidate: ...


class RiskAssessor(Protocol):
    def assess(self, request: SizingRequest, *, now: datetime) -> RiskDecision: ...


class SignalRecordLike(Protocol):
    id: UUID


class SignalRecorder(Protocol):
    async def record(
        self,
        *,
        user_id: UUID,
        portfolio_id: UUID,
        instrument_id: UUID,
        candidate: SignalCandidate,
        risk_decision: RiskDecision | None,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        prediction_id: UUID | None = None,
    ) -> SignalRecordLike: ...


class ExecutionEngine(Protocol):
    def open_position(
        self,
        *,
        position_id: str,
        order: Order,
        market: MarketSnapshot,
        instrument: InstrumentKind,
        side: PositionSide,
        leverage: int,
        stop_loss: Decimal,
        take_profit: Decimal,
        available_cash: Decimal,
    ) -> OpenPositionResult: ...

    def close_position(self, position: PaperPosition, market: MarketSnapshot) -> CloseResult: ...


class PaperStore(Protocol):
    async def has_order(self, idempotency_key: str) -> bool: ...

    async def reserve_order(
        self,
        *,
        portfolio_id: UUID,
        instrument_id: UUID,
        order: Order,
        idempotency_key: str,
    ) -> PaperPersistenceResult: ...

    async def record_open(
        self,
        *,
        portfolio_id: UUID,
        signal_id: UUID | None,
        instrument_id: UUID,
        order: Order,
        result: OpenPositionResult,
        reference_price: Decimal,
        idempotency_key: str,
        base_asset: str,
        quote_asset: str = "USDT",
    ) -> PaperPersistenceResult: ...

    async def record_close(
        self,
        *,
        portfolio_id: UUID,
        signal_id: UUID | None,
        instrument_id: UUID,
        result: CloseResult,
        reference_price: Decimal,
        idempotency_key: str,
        base_asset: str,
        quote_asset: str = "USDT",
    ) -> PaperPersistenceResult: ...


class TradingWorker:
    def __init__(
        self,
        *,
        feature_pipeline: FeatureBuilder,
        active_model: PredictionModel,
        signal_policy: CandidatePolicy,
        risk_engine: RiskAssessor,
        signal_service: SignalRecorder,
        execution_engine: ExecutionEngine,
        paper_repository: PaperStore,
        maximum_observation_age: timedelta = timedelta(minutes=2),
        stop_distance: Decimal = Decimal("0.02"),
        reward_to_risk: Decimal = Decimal("2"),
        execution_costs: ExecutionCosts | None = None,
        venue_constraints: VenueConstraints | None = None,
        prediction_recorder: PredictionRecorder | None = None,
    ) -> None:
        if maximum_observation_age < timedelta(0):
            raise ValueError("maximum_observation_age cannot be negative")
        if stop_distance <= 0 or stop_distance >= 1:
            raise ValueError("stop_distance must be in (0, 1)")
        if reward_to_risk <= 0:
            raise ValueError("reward_to_risk must be positive")
        self._features = feature_pipeline
        self._model = active_model
        self._policy = signal_policy
        self._risk = risk_engine
        self._signals = signal_service
        self._execution = execution_engine
        self._paper = paper_repository
        self._maximum_age = maximum_observation_age
        self._stop_distance = stop_distance
        self._reward_to_risk = reward_to_risk
        self._execution_costs = execution_costs or ExecutionCosts()
        self._venue_constraints = venue_constraints or VenueConstraints()
        self._predictions = prediction_recorder

    async def process(self, item: TradingWorkItem, *, now: datetime | None = None) -> WorkerOutcome:
        processed_at = now or datetime.now(UTC)
        if processed_at.tzinfo is None or processed_at.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        invalid = self._observation_error(item)
        if invalid is not None:
            return self._outcome(
                WorkerStatus.INVALID_OBSERVATION,
                "invalid-observation",
                processed_at,
                detail=invalid,
            )
        business_key = self.business_fingerprint(item)
        if await self._paper.has_order(business_key):
            return self._outcome(WorkerStatus.DUPLICATE, business_key, processed_at)
        age = processed_at - item.observation.data_cutoff
        if age > self._maximum_age or age < timedelta(seconds=-1):
            return self._outcome(
                WorkerStatus.STALE,
                business_key,
                processed_at,
                detail=f"observation age {age} is outside allowed range",
            )

        vector = self._features.build(item.observation)
        prediction = self._model.predict(
            as_of=vector.as_of,
            feature_names=vector.names,
            values=vector.values,
        )
        prediction_id = (
            await self._predictions.record(item, prediction, vector)
            if self._predictions is not None
            else None
        )
        candidate = self._policy.create_candidate(
            instrument=item.observation.instrument,
            market_type=item.market_type,
            reference_price=float((item.market.bid + item.market.ask) / Decimal("2")),
            horizon=item.horizon,
            prediction=prediction,
            current_position=self._active_signal_position(item),
        )
        if candidate.valid_until.tzinfo is None or candidate.valid_until.utcoffset() is None:
            return self._outcome(
                WorkerStatus.INVALID_OBSERVATION,
                business_key,
                processed_at,
                candidate=candidate,
                detail="signal valid_until must be timezone-aware",
            )
        if processed_at >= candidate.valid_until:
            return self._outcome(
                WorkerStatus.SIGNAL_EXPIRED,
                business_key,
                processed_at,
                candidate=candidate,
            )
        if candidate.action is SignalAction.HOLD:
            return self._outcome(WorkerStatus.HOLD, business_key, processed_at, candidate=candidate)
        if candidate.action is SignalAction.CLOSE:
            return await self._close(
                item,
                candidate,
                business_key,
                processed_at,
                prediction_id=prediction_id,
            )

        direction = self._risk_direction(candidate.action)
        stop_loss, take_profit = self._exit_levels(
            Decimal(str(candidate.reference_price)), direction
        )
        risk_decision = self._risk.assess(
            SizingRequest(
                direction=direction,
                entry_price=Decimal(str(candidate.reference_price)),
                stop_price=stop_loss,
                market=self._market_guard(item),
                portfolio=item.portfolio,
                execution_costs=self._execution_costs,
                venue=self._venue_constraints,
            ),
            now=processed_at,
        )
        if not risk_decision.approved:
            return self._outcome(
                WorkerStatus.RISK_REJECTED,
                business_key,
                processed_at,
                candidate=candidate,
                risk_decision=risk_decision,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )
        sizing = risk_decision.sizing
        assert sizing is not None
        order = Order(
            order_id=business_key,
            symbol=item.observation.instrument,
            side=OrderSide.SELL if direction is PositionDirection.SHORT else OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=sizing.quantity,
        )
        reservation = await self._paper.reserve_order(
            portfolio_id=item.portfolio_id,
            instrument_id=item.instrument_id,
            order=order,
            idempotency_key=business_key,
        )
        if not reservation.created:
            return self._outcome(WorkerStatus.DUPLICATE, business_key, processed_at)
        signal = await self._signals.record(
            user_id=item.user_id,
            portfolio_id=item.portfolio_id,
            instrument_id=item.instrument_id,
            candidate=candidate,
            risk_decision=risk_decision,
            stop_loss=stop_loss,
            take_profit=take_profit,
            prediction_id=prediction_id,
        )
        result = self._execution.open_position(
            position_id=f"position:{business_key}",
            order=order,
            market=item.market,
            instrument=(
                InstrumentKind.SPOT
                if direction is PositionDirection.SPOT
                else InstrumentKind.PERPETUAL
            ),
            side=PositionSide(direction.value),
            leverage=sizing.leverage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            available_cash=item.portfolio.available_cash,
        )
        persisted = await self._paper.record_open(
            portfolio_id=item.portfolio_id,
            signal_id=signal.id,
            instrument_id=item.instrument_id,
            order=order,
            result=result,
            reference_price=Decimal(str(candidate.reference_price)),
            idempotency_key=business_key,
            base_asset=item.base_asset,
            quote_asset=item.quote_asset,
        )
        status = (
            WorkerStatus.EXECUTED
            if result.execution.status is OrderStatus.FILLED
            else WorkerStatus.EXECUTION_REJECTED
        )
        return self._outcome(
            status,
            business_key,
            processed_at,
            candidate=candidate,
            risk_decision=risk_decision,
            signal_id=signal.id,
            paper_order_id=persisted.order_id,
            stop_loss=stop_loss,
            take_profit=take_profit,
            detail=result.execution.rejection_reason,
        )

    async def _close(
        self,
        item: TradingWorkItem,
        candidate: SignalCandidate,
        business_key: str,
        processed_at: datetime,
        *,
        prediction_id: UUID | None,
    ) -> WorkerOutcome:
        if item.active_position is None:
            return self._outcome(
                WorkerStatus.INVALID_OBSERVATION,
                business_key,
                processed_at,
                candidate=candidate,
                detail="CLOSE candidate requires an active paper position",
            )
        closing_order = Order(
            order_id=business_key,
            symbol=item.observation.instrument,
            side=(
                OrderSide.BUY if item.active_position.side is PositionSide.SHORT else OrderSide.SELL
            ),
            order_type=OrderType.MARKET,
            quantity=item.active_position.quantity,
        )
        reservation = await self._paper.reserve_order(
            portfolio_id=item.portfolio_id,
            instrument_id=item.instrument_id,
            order=closing_order,
            idempotency_key=business_key,
        )
        if not reservation.created:
            return self._outcome(WorkerStatus.DUPLICATE, business_key, processed_at)
        signal = await self._signals.record(
            user_id=item.user_id,
            portfolio_id=item.portfolio_id,
            instrument_id=item.instrument_id,
            candidate=candidate,
            risk_decision=None,
            prediction_id=prediction_id,
        )
        result = self._execution.close_position(item.active_position, item.market)
        persisted = await self._paper.record_close(
            portfolio_id=item.portfolio_id,
            signal_id=signal.id,
            instrument_id=item.instrument_id,
            result=result,
            reference_price=Decimal(str(candidate.reference_price)),
            idempotency_key=business_key,
            base_asset=item.base_asset,
            quote_asset=item.quote_asset,
        )
        return self._outcome(
            WorkerStatus.EXECUTED,
            business_key,
            processed_at,
            candidate=candidate,
            signal_id=signal.id,
            paper_order_id=persisted.order_id,
        )

    @staticmethod
    def _market_guard(item: TradingWorkItem) -> MarketGuard:
        return MarketGuard(
            observed_at=item.market.observed_at,
            bid=item.market.bid,
            ask=item.market.ask,
        )

    @staticmethod
    def _risk_direction(action: SignalAction) -> PositionDirection:
        return {
            SignalAction.BUY: PositionDirection.SPOT,
            SignalAction.LONG: PositionDirection.LONG,
            SignalAction.SHORT: PositionDirection.SHORT,
        }[action]

    def _exit_levels(
        self, reference: Decimal, direction: PositionDirection
    ) -> tuple[Decimal, Decimal]:
        reward = self._stop_distance * self._reward_to_risk
        if direction in {PositionDirection.SPOT, PositionDirection.LONG}:
            return reference * (1 - self._stop_distance), reference * (1 + reward)
        return reference * (1 + self._stop_distance), reference * (1 - reward)

    @staticmethod
    def _active_signal_position(item: TradingWorkItem) -> ActivePosition | None:
        if item.active_position is None:
            return None
        return ActivePosition(
            direction=SignalPositionDirection(item.active_position.side.value),
            stop_loss=float(item.active_position.stop_loss),
            liquidation_price=(
                float(item.active_position.liquidation_price)
                if item.active_position.liquidation_price is not None
                else None
            ),
        )

    @staticmethod
    def _observation_error(item: TradingWorkItem) -> str | None:
        observation = item.observation
        if observation.as_of.tzinfo is None or observation.data_cutoff.tzinfo is None:
            return "observation timestamps must be timezone-aware"
        if observation.data_cutoff > observation.as_of:
            return "data_cutoff is later than as_of"
        if not observation.candles:
            return "closed observation requires at least one candle"
        if any(candle.close_time > observation.data_cutoff for candle in observation.candles):
            return "observation contains a candle later than data_cutoff"
        source_times = (
            observation.orderbook_at,
            observation.trade_flow_at,
            observation.derivatives_at,
        )
        if any(value.tzinfo is None or value.utcoffset() is None for value in source_times):
            return "source timestamps must be timezone-aware"
        if any(value > observation.data_cutoff for value in source_times):
            return "observation contains source data later than data_cutoff"
        expected_symbol = f"{item.base_asset.upper()}{item.quote_asset.upper()}"
        if TradingWorker._normalized_symbol(observation.instrument) != expected_symbol:
            return "observation instrument does not match base_asset/quote_asset"
        if (
            item.active_position is not None
            and TradingWorker._normalized_symbol(item.active_position.symbol) != expected_symbol
        ):
            return "active position symbol does not match observation instrument"
        return None

    @staticmethod
    def _normalized_symbol(value: str) -> str:
        normalized = value.upper().split(":", maxsplit=1)[0]
        return "".join(character for character in normalized if character.isalnum())

    @staticmethod
    def business_fingerprint(item: TradingWorkItem) -> str:
        material = "|".join(
            (
                "v1",
                str(item.portfolio_id),
                str(item.instrument_id),
                TradingWorker._normalized_symbol(item.observation.instrument),
                item.market_type.value,
                item.horizon,
                item.observation.data_cutoff.astimezone(UTC).isoformat(timespec="microseconds"),
            )
        )
        return f"worker:v1:{hashlib.sha256(material.encode()).hexdigest()}"

    @staticmethod
    def _outcome(
        status: WorkerStatus,
        idempotency_key: str,
        processed_at: datetime,
        *,
        candidate: SignalCandidate | None = None,
        risk_decision: RiskDecision | None = None,
        signal_id: UUID | None = None,
        paper_order_id: UUID | None = None,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        detail: str | None = None,
    ) -> WorkerOutcome:
        return WorkerOutcome(
            status=status,
            idempotency_key=idempotency_key,
            candidate=candidate,
            risk_decision=risk_decision,
            signal_id=signal_id,
            paper_order_id=paper_order_id,
            stop_loss=stop_loss,
            take_profit=take_profit,
            detail=detail,
            processed_at=processed_at,
        )
