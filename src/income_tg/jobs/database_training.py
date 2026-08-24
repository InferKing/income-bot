from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from income_tg.jobs.retraining import (
    CandidateAssessment,
    CandidateDetails,
    CandidateTrade,
    RetrainingOutcome,
    RetrainingWorkflow,
)
from income_tg.models.dataset import LabeledDataset, chronological_train_test, load_labeled_dataset
from income_tg.models.evaluation import AdmissionCriteria
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
    round_trip_cost: float = 0.0015
    minimum_edge: float = 0.0005
    target_action_fraction: float = 0.20

    @property
    def minimum_actionable_return(self) -> float:
        return self.round_trip_cost + self.minimum_edge


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
                minimum_actionable_return=self.target.minimum_actionable_return,
            )
        training, _ = chronological_train_test(labeled)
        return train_ensemble(
            training.dataset,
            target_action_fraction=self.target.target_action_fraction,
        )


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
                minimum_actionable_return=self.target.minimum_actionable_return,
            )
        _, test = chronological_train_test(labeled)
        challenger_metrics = _strategy_metrics(challenger, test, self.target)
        champion_return = (
            _strategy_metrics(champion, test, self.target).net_return
            if champion is not None and champion.feature_names == test.dataset.feature_names
            else None
        )
        details = CandidateDetails(
            test_from=(test.dataset.timestamps[0] if test.dataset.timestamps else None),
            test_to=(test.dataset.timestamps[-1] if test.dataset.timestamps else None),
            confidence_threshold=challenger.confidence_threshold,
            long_trades=challenger_metrics.long_trades,
            short_trades=challenger_metrics.short_trades,
            skipped_points=len(test.dataset.timestamps) - challenger_metrics.trades,
            winning_trades=challenger_metrics.winning_trades,
            losing_trades=challenger_metrics.losing_trades,
            breakeven_trades=challenger_metrics.breakeven_trades,
            win_rate=challenger_metrics.win_rate,
            gross_profit=challenger_metrics.gross_profit,
            gross_loss=challenger_metrics.gross_loss,
            total_costs=challenger_metrics.total_costs,
            average_trade_return=challenger_metrics.average_trade_return,
            best_trade_return=challenger_metrics.best_trade_return,
            worst_trade_return=challenger_metrics.worst_trade_return,
            average_confidence=challenger_metrics.average_confidence,
            median_confidence=challenger_metrics.median_confidence,
            p95_confidence=challenger_metrics.p95_confidence,
            max_confidence=challenger_metrics.max_confidence,
            signals_by_threshold=challenger_metrics.signals_by_threshold,
            label_short=int((test.dataset.targets == -1).sum()),
            label_no_trade=int((test.dataset.targets == 0).sum()),
            label_long=int((test.dataset.targets == 1).sum()),
            recent_return=challenger_metrics.recent_return,
            baseline_return=0.0,
            champion_return=champion_return,
            recent_trades=challenger_metrics.recent_trades,
        )
        return CandidateAssessment(
            net_return=challenger_metrics.net_return,
            max_drawdown=challenger_metrics.max_drawdown,
            profit_factor=challenger_metrics.profit_factor,
            closed_trades=challenger_metrics.trades,
            test_samples=len(test.dataset.timestamps),
            beats_baseline=challenger_metrics.net_return > 0.0,
            recent_period_stable=challenger_metrics.recent_return >= 0,
            beats_champion=(
                champion_return is None or challenger_metrics.net_return > champion_return
            ),
            details=details,
        )


class PersistedRetrainingWorkflow:
    """Records every file-registry admission outcome in the relational audit trail."""

    def __init__(
        self,
        delegate: RetrainingWorkflow,
        session_factory: async_sessionmaker[AsyncSession],
        registry: FileModelRegistry,
        target: TrainingTarget,
        criteria: AdmissionCriteria | None = None,
    ) -> None:
        self._delegate = delegate
        self._session_factory = session_factory
        self._registry = registry
        self._target = target
        self._criteria = criteria or AdmissionCriteria()

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
                    "round_trip_cost": self._target.round_trip_cost,
                    "minimum_edge": self._target.minimum_edge,
                    "minimum_actionable_return": self._target.minimum_actionable_return,
                    "target_action_fraction": self._target.target_action_fraction,
                    "max_drawdown": self._criteria.max_drawdown,
                    "min_profit_factor": self._criteria.min_profit_factor,
                    "min_closed_trade_fraction": self._criteria.min_closed_trade_fraction,
                },
                metrics={
                    "candidate_version": outcome.challenger_version,
                    "net_return": outcome.assessment.net_return,
                    "max_drawdown": outcome.assessment.max_drawdown,
                    "profit_factor": outcome.assessment.profit_factor,
                    "closed_trades": outcome.assessment.closed_trades,
                    "test_samples": outcome.assessment.test_samples,
                    "closed_trade_fraction": (
                        outcome.assessment.closed_trades / outcome.assessment.test_samples
                    ),
                    "admission_reasons": list(outcome.decision.reasons),
                    **_candidate_detail_metrics(outcome.assessment.details),
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
    long_trades: int
    short_trades: int
    winning_trades: int
    losing_trades: int
    breakeven_trades: int
    win_rate: float
    gross_profit: float
    gross_loss: float
    total_costs: float
    average_trade_return: float
    best_trade_return: float
    worst_trade_return: float
    average_confidence: float
    median_confidence: float
    p95_confidence: float
    max_confidence: float
    signals_by_threshold: tuple[tuple[float, int], ...]
    recent_trades: tuple[CandidateTrade, ...]


def _strategy_metrics(
    model: EnsembleModel,
    labeled: LabeledDataset,
    target: TrainingTarget,
) -> _StrategyMetrics:
    pnls: list[float] = []
    trades: list[CandidateTrade] = []
    directional_confidences: list[float] = []
    actionable_confidences: list[float] = []
    signal_counts = {threshold: 0 for threshold in (0.55, 0.60, 0.65, 0.70)}
    long_trades = 0
    short_trades = 0
    for index, timestamp in enumerate(labeled.dataset.timestamps):
        prediction = model.predict(
            as_of=timestamp,
            feature_names=labeled.dataset.feature_names,
            values=tuple(float(value) for value in labeled.dataset.features[index]),
        )
        direction = 0
        confidence = max(prediction.probability_up, prediction.probability_down)
        directional_confidences.append(confidence)
        direction_is_actionable = confidence > prediction.probability_no_trade
        if direction_is_actionable:
            for threshold in signal_counts:
                signal_counts[threshold] += int(confidence >= threshold)
        if direction_is_actionable and prediction.probability_up >= model.confidence_threshold:
            direction = 1
            confidence = prediction.probability_up
        elif direction_is_actionable and prediction.probability_down >= model.confidence_threshold:
            direction = -1
            confidence = prediction.probability_down
        if direction:
            actionable_confidences.append(confidence)
            pnl = labeled.forward_returns[index] * direction - target.round_trip_cost
            pnls.append(pnl)
            long_trades += int(direction > 0)
            short_trades += int(direction < 0)
            trades.append(
                CandidateTrade(
                    occurred_at=timestamp,
                    direction="LONG" if direction > 0 else "SHORT",
                    confidence=confidence,
                    net_return=pnl,
                )
            )
    if not pnls:
        return _StrategyMetrics(
            net_return=0.0,
            max_drawdown=0.0,
            profit_factor=0.0,
            trades=0,
            recent_return=0.0,
            long_trades=0,
            short_trades=0,
            winning_trades=0,
            losing_trades=0,
            breakeven_trades=0,
            win_rate=0.0,
            gross_profit=0.0,
            gross_loss=0.0,
            total_costs=0.0,
            average_trade_return=0.0,
            best_trade_return=0.0,
            worst_trade_return=0.0,
            average_confidence=0.0,
            median_confidence=_percentile(directional_confidences, 0.50),
            p95_confidence=_percentile(directional_confidences, 0.95),
            max_confidence=max(directional_confidences, default=0.0),
            signals_by_threshold=tuple(signal_counts.items()),
            recent_trades=(),
        )
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for pnl in pnls:
        equity *= 1 + pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak)
    gains = sum(value for value in pnls if value > 0)
    losses = abs(sum(value for value in pnls if value < 0))
    winning_trades = sum(value > 0 for value in pnls)
    losing_trades = sum(value < 0 for value in pnls)
    breakeven_trades = len(pnls) - winning_trades - losing_trades
    recent = pnls[max(0, len(pnls) * 3 // 4) :]
    return _StrategyMetrics(
        net_return=equity - 1,
        max_drawdown=max_drawdown,
        profit_factor=gains / losses if losses else (999.0 if gains else 0.0),
        trades=len(pnls),
        recent_return=sum(recent),
        long_trades=long_trades,
        short_trades=short_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        breakeven_trades=breakeven_trades,
        win_rate=winning_trades / len(pnls),
        gross_profit=gains,
        gross_loss=losses,
        total_costs=target.round_trip_cost * len(pnls),
        average_trade_return=sum(pnls) / len(pnls),
        best_trade_return=max(pnls),
        worst_trade_return=min(pnls),
        average_confidence=sum(actionable_confidences) / len(actionable_confidences),
        median_confidence=_percentile(directional_confidences, 0.50),
        p95_confidence=_percentile(directional_confidences, 0.95),
        max_confidence=max(directional_confidences, default=0.0),
        signals_by_threshold=tuple(signal_counts.items()),
        recent_trades=tuple(trades[-5:]),
    )


def _candidate_detail_metrics(details: CandidateDetails | None) -> dict[str, object]:
    if details is None:
        return {}
    return {
        "test_from": details.test_from.isoformat() if details.test_from is not None else None,
        "test_to": details.test_to.isoformat() if details.test_to is not None else None,
        "confidence_threshold": details.confidence_threshold,
        "long_trades": details.long_trades,
        "short_trades": details.short_trades,
        "skipped_points": details.skipped_points,
        "winning_trades": details.winning_trades,
        "losing_trades": details.losing_trades,
        "breakeven_trades": details.breakeven_trades,
        "win_rate": details.win_rate,
        "gross_profit": details.gross_profit,
        "gross_loss": details.gross_loss,
        "total_costs": details.total_costs,
        "average_trade_return": details.average_trade_return,
        "best_trade_return": details.best_trade_return,
        "worst_trade_return": details.worst_trade_return,
        "average_confidence": details.average_confidence,
        "median_confidence": details.median_confidence,
        "p95_confidence": details.p95_confidence,
        "max_confidence": details.max_confidence,
        "signals_by_threshold": {
            str(threshold): count for threshold, count in details.signals_by_threshold
        },
        "label_short": details.label_short,
        "label_no_trade": details.label_no_trade,
        "label_long": details.label_long,
        "recent_return": details.recent_return,
        "baseline_return": details.baseline_return,
        "champion_return": details.champion_return,
        "recent_trades": [
            {
                "occurred_at": item.occurred_at.isoformat(),
                "direction": item.direction,
                "confidence": item.confidence,
                "net_return": item.net_return,
            }
            for item in details.recent_trades
        ],
    }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * fraction)
    return ordered[index]
