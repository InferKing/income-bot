from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from income_tg.jobs.activation import AtomicModelActivator
from income_tg.jobs.models import JobDefinition
from income_tg.jobs.scheduler import next_weekly_run
from income_tg.models.evaluation import (
    AdmissionCriteria,
    AdmissionDecision,
    evaluate_admission,
)
from income_tg.models.inference import EnsembleModel
from income_tg.models.registry import RegisteredModel


@dataclass(frozen=True, slots=True)
class CandidateAssessment:
    net_return: float
    max_drawdown: float
    profit_factor: float
    closed_trades: int
    test_samples: int
    beats_baseline: bool
    recent_period_stable: bool
    beats_champion: bool = True


class RetrainingStatus(StrEnum):
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass(frozen=True, slots=True)
class RetrainingOutcome:
    status: RetrainingStatus
    challenger_version: str
    decision: AdmissionDecision
    assessment: CandidateAssessment
    rollback_reason: str | None = None

    def summary(self) -> str:
        reasons = ",".join(self.decision.reasons)
        suffix = f":{reasons}" if reasons else ""
        return f"{self.status.value}:{self.challenger_version}{suffix}"


class CandidateTrainer(Protocol):
    async def train(self) -> EnsembleModel: ...


class CandidateEvaluator(Protocol):
    async def evaluate(
        self, challenger: EnsembleModel, champion: EnsembleModel | None
    ) -> CandidateAssessment: ...


class ModelRegistry(Protocol):
    def register(self, model: EnsembleModel, *, stage: str = "CHALLENGER") -> RegisteredModel: ...

    def load_champion(self) -> EnsembleModel: ...


class ActivationCheck(Protocol):
    async def __call__(self, model: EnsembleModel) -> bool: ...


class RetrainingRunner(Protocol):
    async def run(self) -> RetrainingOutcome: ...


class RetrainingWorkflow:
    def __init__(
        self,
        trainer: CandidateTrainer,
        evaluator: CandidateEvaluator,
        registry: ModelRegistry,
        activator: AtomicModelActivator,
        *,
        criteria: AdmissionCriteria | None = None,
        activation_check: ActivationCheck | None = None,
    ) -> None:
        self.trainer = trainer
        self.evaluator = evaluator
        self.registry = registry
        self.activator = activator
        self.criteria = criteria
        self.activation_check = activation_check

    async def run(self) -> RetrainingOutcome:
        challenger = await self.trainer.train()
        registered = self.registry.register(challenger, stage="CHALLENGER")
        try:
            champion = self.registry.load_champion()
        except FileNotFoundError:
            champion = None
        assessment = await self.evaluator.evaluate(challenger, champion)
        decision = evaluate_admission(
            net_return=assessment.net_return,
            max_drawdown=assessment.max_drawdown,
            profit_factor=assessment.profit_factor,
            closed_trades=assessment.closed_trades,
            test_samples=assessment.test_samples,
            beats_baseline=assessment.beats_baseline,
            recent_period_stable=assessment.recent_period_stable,
            beats_champion=assessment.beats_champion,
            criteria=self.criteria,
        )
        if not decision.accepted:
            return RetrainingOutcome(
                RetrainingStatus.REJECTED,
                registered.version,
                decision,
                assessment,
            )

        receipt = self.activator.activate(registered.version)
        if self.activation_check is not None:
            try:
                healthy = await self.activation_check(challenger)
            except Exception as error:
                self.activator.rollback(receipt)
                return RetrainingOutcome(
                    RetrainingStatus.ROLLED_BACK,
                    registered.version,
                    decision,
                    assessment,
                    f"{type(error).__name__}: {error}"[:1000],
                )
            if not healthy:
                self.activator.rollback(receipt)
                return RetrainingOutcome(
                    RetrainingStatus.ROLLED_BACK,
                    registered.version,
                    decision,
                    assessment,
                    "POST_ACTIVATION_CHECK_FAILED",
                )
        return RetrainingOutcome(
            RetrainingStatus.PROMOTED,
            registered.version,
            decision,
            assessment,
        )


class WeeklyRetrainingJob:
    def __init__(self, workflow: RetrainingRunner) -> None:
        self.workflow = workflow
        self.last_outcome: RetrainingOutcome | None = None

    async def __call__(self, scheduled_for: datetime) -> str:
        del scheduled_for
        self.last_outcome = await self.workflow.run()
        return self.last_outcome.summary()


def weekly_retraining_definition(
    workflow: RetrainingRunner,
    now: datetime,
    *,
    weekday: int = 0,
    hour: int = 3,
) -> JobDefinition:
    first_run = next_weekly_run(now, weekday=weekday, hour=hour)
    return JobDefinition(
        name="weekly-retraining",
        interval=timedelta(days=7),
        initial_delay=first_run - now,
        handler=WeeklyRetrainingJob(workflow),
    )
