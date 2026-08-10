from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

from income_tg.jobs import (
    ActivationReceipt,
    CandidateAssessment,
    RetrainingStatus,
    RetrainingWorkflow,
    WeeklyRetrainingJob,
)
from income_tg.models.inference import EnsembleModel
from income_tg.models.registry import RegisteredModel


def fake_model(version: str) -> EnsembleModel:
    return cast(EnsembleModel, SimpleNamespace(version=version))


class FakeTrainer:
    def __init__(self, model: EnsembleModel) -> None:
        self.model = model
        self.calls = 0

    async def train(self) -> EnsembleModel:
        self.calls += 1
        return self.model


class FakeEvaluator:
    def __init__(self, assessment: CandidateAssessment) -> None:
        self.assessment = assessment
        self.champions: list[EnsembleModel | None] = []

    async def evaluate(
        self, challenger: EnsembleModel, champion: EnsembleModel | None
    ) -> CandidateAssessment:
        del challenger
        self.champions.append(champion)
        return self.assessment


class FakeRegistry:
    def __init__(self, champion: EnsembleModel | None = None) -> None:
        self.champion = champion
        self.registered: list[tuple[str, str]] = []

    def register(self, model: EnsembleModel, *, stage: str = "CHALLENGER") -> RegisteredModel:
        self.registered.append((model.version, stage))
        return RegisteredModel(model.version, "artifact", "hash", stage, "now")

    def load_champion(self) -> EnsembleModel:
        if self.champion is None:
            raise FileNotFoundError
        return self.champion


class FakeActivator:
    def __init__(self, previous: str | None = "v1") -> None:
        self.previous = previous
        self.activations: list[str] = []
        self.rollbacks: list[ActivationReceipt] = []

    def activate(self, version: str) -> ActivationReceipt:
        self.activations.append(version)
        return ActivationReceipt(version, self.previous)

    def rollback(self, receipt: ActivationReceipt) -> None:
        self.rollbacks.append(receipt)


def passing_assessment() -> CandidateAssessment:
    return CandidateAssessment(0.2, 0.1, 1.5, 150, True, True)


async def test_accepted_challenger_is_registered_evaluated_and_promoted() -> None:
    candidate = fake_model("v2")
    trainer = FakeTrainer(candidate)
    evaluator = FakeEvaluator(passing_assessment())
    registry = FakeRegistry(fake_model("v1"))
    activator = FakeActivator()
    workflow = RetrainingWorkflow(trainer, evaluator, registry, activator)

    outcome = await workflow.run()

    assert outcome.status is RetrainingStatus.PROMOTED
    assert registry.registered == [("v2", "CHALLENGER")]
    assert evaluator.champions[0] is registry.champion
    assert activator.activations == ["v2"]
    assert activator.rollbacks == []


async def test_rejected_challenger_never_changes_active_model() -> None:
    assessment = CandidateAssessment(-0.01, 0.2, 0.8, 10, False, False)
    activator = FakeActivator()
    workflow = RetrainingWorkflow(
        FakeTrainer(fake_model("bad")),
        FakeEvaluator(assessment),
        FakeRegistry(),
        activator,
    )

    outcome = await workflow.run()

    assert outcome.status is RetrainingStatus.REJECTED
    assert "NET_RETURN_NOT_POSITIVE" in outcome.decision.reasons
    assert activator.activations == []


async def test_failed_post_activation_check_rolls_back() -> None:
    async def unhealthy(model: EnsembleModel) -> bool:
        del model
        return False

    activator = FakeActivator()
    workflow = RetrainingWorkflow(
        FakeTrainer(fake_model("v2")),
        FakeEvaluator(passing_assessment()),
        FakeRegistry(),
        activator,
        activation_check=unhealthy,
    )

    outcome = await workflow.run()

    assert outcome.status is RetrainingStatus.ROLLED_BACK
    assert outcome.rollback_reason == "POST_ACTIVATION_CHECK_FAILED"
    assert activator.rollbacks == [ActivationReceipt("v2", "v1")]


async def test_weekly_job_exposes_last_outcome_and_summary() -> None:
    workflow = RetrainingWorkflow(
        FakeTrainer(fake_model("v2")),
        FakeEvaluator(passing_assessment()),
        FakeRegistry(),
        FakeActivator(),
    )
    job = WeeklyRetrainingJob(workflow)

    summary = await job(datetime(2026, 8, 10, tzinfo=UTC))

    assert summary == "PROMOTED:v2"
    assert job.last_outcome is not None
