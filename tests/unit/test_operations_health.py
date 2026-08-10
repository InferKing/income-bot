import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from income_tg.operations.health import (
    Component,
    HealthAggregator,
    HealthLevel,
    HealthObservation,
    HealthReport,
    ProbeResult,
    ReadinessPolicy,
)

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def observation(
    component: Component,
    *,
    level: HealthLevel = HealthLevel.HEALTHY,
    checked_at: datetime = NOW,
) -> HealthObservation:
    return HealthObservation(
        component=component,
        level=level,
        code=f"{component.value}_{level.value}",
        checked_at=checked_at,
        latency_ms=1,
    )


def report(*items: HealthObservation) -> HealthReport:
    return HealthReport(observations=tuple(items), generated_at=NOW)


def test_all_required_fresh_components_are_ready() -> None:
    health = report(*(observation(component) for component in Component))

    decision = ReadinessPolicy().evaluate(health, now=NOW)

    assert decision.ready
    assert decision.reasons == ()
    assert health.level is HealthLevel.HEALTHY


def test_readiness_reports_missing_stale_and_unhealthy_components() -> None:
    health = report(
        observation(Component.DATABASE, checked_at=NOW - timedelta(seconds=31)),
        observation(Component.MARKET, level=HealthLevel.UNHEALTHY),
        observation(Component.MODEL),
    )

    decision = ReadinessPolicy().evaluate(health, now=NOW)

    assert not decision.ready
    assert decision.reasons == (
        "BOT_MISSING",
        "DATABASE_STALE",
        "MARKET_UNHEALTHY",
    )


def test_degraded_component_can_be_explicitly_allowed() -> None:
    health = report(
        observation(Component.DATABASE),
        observation(Component.MARKET, level=HealthLevel.DEGRADED),
        observation(Component.MODEL),
        observation(Component.BOT),
    )
    strict = ReadinessPolicy()
    tolerant = ReadinessPolicy(degraded_components_allowed=frozenset({Component.MARKET}))

    assert not strict.evaluate(health, now=NOW).ready
    assert tolerant.evaluate(health, now=NOW).ready
    assert health.level is HealthLevel.DEGRADED


def test_clock_skew_is_not_ready() -> None:
    health = report(
        *(observation(item, checked_at=NOW + timedelta(seconds=1)) for item in Component)
    )

    decision = ReadinessPolicy().evaluate(health, now=NOW)

    assert decision.reasons == tuple(
        f"{item.value}_CLOCK_SKEW" for item in sorted(Component, key=lambda item: item.value)
    )


@pytest.mark.asyncio
async def test_aggregator_collects_probes_in_stable_component_order() -> None:
    async def healthy() -> ProbeResult:
        return ProbeResult(HealthLevel.HEALTHY, "OK")

    aggregator = HealthAggregator(
        {
            Component.MODEL: healthy,
            Component.DATABASE: healthy,
            Component.BOT: healthy,
            Component.MARKET: healthy,
        }
    )

    collected = await aggregator.collect(now=NOW)

    assert tuple(item.component for item in collected.observations) == tuple(
        sorted(Component, key=lambda item: item.value)
    )
    assert all(item.level is HealthLevel.HEALTHY for item in collected.observations)


@pytest.mark.asyncio
async def test_probe_exception_is_sanitized_and_secret_is_not_exposed() -> None:
    secret = "postgresql://user:super-secret@host/database"

    async def failing() -> ProbeResult:
        raise RuntimeError(secret)

    collected = await HealthAggregator({Component.DATABASE: failing}).collect(now=NOW)

    item = collected.observations[0]
    assert item.code == "PROBE_FAILED"
    assert item.level is HealthLevel.UNHEALTHY
    assert secret not in repr(collected)


@pytest.mark.asyncio
async def test_probe_timeout_becomes_unhealthy() -> None:
    async def slow() -> ProbeResult:
        await asyncio.sleep(0.05)
        return ProbeResult(HealthLevel.HEALTHY, "OK")

    collected = await HealthAggregator(
        {Component.MARKET: slow},
        timeout=timedelta(milliseconds=1),
    ).collect(now=NOW)

    assert collected.observations[0].code == "PROBE_TIMEOUT"


def test_duplicate_component_observations_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        report(observation(Component.BOT), observation(Component.BOT))


def test_naive_health_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        observation(Component.BOT, checked_at=datetime(2026, 8, 10))
