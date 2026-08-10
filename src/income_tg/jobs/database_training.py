from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from income_tg.jobs.retraining import CandidateAssessment, RetrainingOutcome, RetrainingWorkflow
from income_tg.models.dataset import LabeledDataset, chronological_train_test, load_labeled_dataset
from income_tg.models.inference import EnsembleModel
from income_tg.models.registry import FileModelRegistry
from income_tg.models.training import train_ensemble
from income_tg.storage.trading_models import (
    FeatureVectorRecord,
    ModelVersionRecord,
    TrainingRunRecord,
)


@dataclass(frozen=True, slots=True)
class TrainingTarget:
    instrument_id: UUID
    horizon: str
    horizon_duration: timedelta
    confidence_threshold: float = 0.70
    round_trip_cost: float = 0.0015


class DatabaseCandidateTrainer:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        target: TrainingTarget,
    ) -> None:
        self.session_factory = session_factory
        self.target = target

    async def train(self) -> EnsembleModel:
        async with self.session_factory() as session:
            labeled = await load_labeled_dataset(
                session,
                instrument_id=self.target.instrument_id,
                horizon=self.target.horizon,
                horizon_duration=self.target.horizon_duration,
            )
        training, _ = chronological_train_test(labeled)
        return train_ensemble(training.dataset)


class DatabaseCandidateEvaluator:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        target: TrainingTarget,
    ) -> None:
        self.session_factory = session_factory
        self.target = target

    async def evaluate(
        self,
        challenger: EnsembleModel,
        champion: EnsembleModel | None,
    ) -> CandidateAssessment:
        async with self.session_factory() as session:
            labeled = await load_labeled_dataset(
                session,
                instrument_id=self.target.instrument_id,
                horizon=self.target.horizon,
                horizon_duration=self.target.horizon_duration,
            )
        _, test = chronological_train_test(labeled)
        challenger_metrics = _strategy_metrics(challenger, test, self.target)
        champion_return = (
            _strategy_metrics(champion, test, self.target).net_return
            if champion is not None and champion.feature_names == test.dataset.feature_names
            else float("-inf")
        )
        return CandidateAssessment(
            net_return=challenger_metrics.net_return,
            max_drawdown=challenger_metrics.max_drawdown,
            profit_factor=challenger_metrics.profit_factor,
            closed_trades=challenger_metrics.trades,
            beats_baseline=challenger_metrics.net_return > 0.0,
            recent_period_stable=challenger_metrics.recent_return >= 0,
            beats_champion=challenger_metrics.net_return > champion_return,
        )


class PersistedRetrainingWorkflow:
    """Records every file-registry admission outcome in the relational audit trail."""

    def __init__(
        self,
        delegate: RetrainingWorkflow,
        session_factory: async_sessionmaker[AsyncSession],
        registry: FileModelRegistry,
        target: TrainingTarget,
    ) -> None:
        self._delegate = delegate
        self._session_factory = session_factory
        self._registry = registry
        self._target = target

    async def run(self) -> RetrainingOutcome:
        started_at = datetime.now(UTC)
        outcome = await self._delegate.run()
        metadata = self._registry.describe(outcome.challenger_version)
        async with self._session_factory() as session, session.begin():
            bounds = await session.execute(
                select(
                    func.min(FeatureVectorRecord.as_of),
                    func.max(FeatureVectorRecord.as_of),
                ).where(
                    FeatureVectorRecord.instrument_id == self._target.instrument_id,
                    FeatureVectorRecord.horizon == self._target.horizon,
                )
            )
            train_from, train_to = bounds.one()
            if train_from is None or train_to is None:
                train_from = train_to = started_at
            run = TrainingRunRecord(
                status=outcome.status.value,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                train_from=train_from,
                train_to=train_to,
                parameters={
                    "instrument_id": str(self._target.instrument_id),
                    "horizon": self._target.horizon,
                    "horizon_seconds": int(self._target.horizon_duration.total_seconds()),
                    "confidence_threshold": self._target.confidence_threshold,
                    "round_trip_cost": self._target.round_trip_cost,
                },
                metrics={
                    "net_return": outcome.assessment.net_return,
                    "max_drawdown": outcome.assessment.max_drawdown,
                    "profit_factor": outcome.assessment.profit_factor,
                    "closed_trades": outcome.assessment.closed_trades,
                    "admission_reasons": list(outcome.decision.reasons),
                },
                error_message=outcome.rollback_reason,
                code_version="0.1.0",
                data_version=f"{train_from.isoformat()}..{train_to.isoformat()}",
            )
            session.add(run)
            await session.flush()
            if outcome.status.value == "PROMOTED":
                await session.execute(
                    update(ModelVersionRecord)
                    .where(ModelVersionRecord.stage == "CHAMPION")
                    .values(stage="RETIRED")
                )
            session.add(
                ModelVersionRecord(
                    training_run_id=run.id,
                    name="ensemble",
                    version=outcome.challenger_version,
                    stage="CHAMPION" if outcome.status.value == "PROMOTED" else "CHALLENGER",
                    artifact_uri=metadata.artifact_path,
                    artifact_hash=metadata.sha256,
                    metrics=run.metrics or {},
                    activated_at=(
                        datetime.now(UTC) if outcome.status.value == "PROMOTED" else None
                    ),
                )
            )
        return outcome


@dataclass(frozen=True, slots=True)
class _StrategyMetrics:
    net_return: float
    max_drawdown: float
    profit_factor: float
    trades: int
    recent_return: float


def _strategy_metrics(
    model: EnsembleModel,
    labeled: LabeledDataset,
    target: TrainingTarget,
) -> _StrategyMetrics:
    pnls: list[float] = []
    for index, timestamp in enumerate(labeled.dataset.timestamps):
        prediction = model.predict(
            as_of=timestamp,
            feature_names=labeled.dataset.feature_names,
            values=tuple(float(value) for value in labeled.dataset.features[index]),
        )
        direction = 0
        if prediction.probability_up >= target.confidence_threshold:
            direction = 1
        elif prediction.probability_down >= target.confidence_threshold:
            direction = -1
        if direction:
            pnls.append(labeled.forward_returns[index] * direction - target.round_trip_cost)
    if not pnls:
        return _StrategyMetrics(0.0, 0.0, 0.0, 0, 0.0)
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for pnl in pnls:
        equity *= 1 + pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak)
    gains = sum(value for value in pnls if value > 0)
    losses = abs(sum(value for value in pnls if value < 0))
    recent = pnls[max(0, len(pnls) * 3 // 4) :]
    return _StrategyMetrics(
        net_return=equity - 1,
        max_drawdown=max_drawdown,
        profit_factor=gains / losses if losses else (999.0 if gains else 0.0),
        trades=len(pnls),
        recent_return=sum(recent),
    )
