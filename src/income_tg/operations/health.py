from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from time import monotonic


class Component(StrEnum):
    DATABASE = "DATABASE"
    MARKET = "MARKET"
    MODEL = "MODEL"
    BOT = "BOT"


class HealthLevel(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    level: HealthLevel
    code: str

    def __post_init__(self) -> None:
        if not self.code or not self.code.replace("_", "").isalnum():
            raise ValueError("probe code must be a non-empty machine-readable token")


@dataclass(frozen=True, slots=True)
class HealthObservation:
    component: Component
    level: HealthLevel
    code: str
    checked_at: datetime
    latency_ms: int

    def __post_init__(self) -> None:
        _aware(self.checked_at, "checked_at")
        if self.latency_ms < 0:
            raise ValueError("latency_ms cannot be negative")
        if not self.code:
            raise ValueError("code cannot be blank")


@dataclass(frozen=True, slots=True)
class HealthReport:
    observations: tuple[HealthObservation, ...]
    generated_at: datetime

    def __post_init__(self) -> None:
        _aware(self.generated_at, "generated_at")
        components = [item.component for item in self.observations]
        if len(components) != len(set(components)):
            raise ValueError("health report cannot contain duplicate components")

    @property
    def level(self) -> HealthLevel:
        levels = {item.level for item in self.observations}
        if HealthLevel.UNHEALTHY in levels:
            return HealthLevel.UNHEALTHY
        if HealthLevel.DEGRADED in levels:
            return HealthLevel.DEGRADED
        if HealthLevel.UNKNOWN in levels or not levels:
            return HealthLevel.UNKNOWN
        return HealthLevel.HEALTHY

    def by_component(self) -> dict[Component, HealthObservation]:
        return {item.component: item for item in self.observations}


@dataclass(frozen=True, slots=True)
class ReadinessDecision:
    ready: bool
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.ready == bool(self.reasons):
            raise ValueError("ready decision and reasons disagree")


@dataclass(frozen=True, slots=True)
class ReadinessPolicy:
    required_components: frozenset[Component] = frozenset(Component)
    max_observation_age: timedelta = timedelta(seconds=30)
    degraded_components_allowed: frozenset[Component] = frozenset()

    def __post_init__(self) -> None:
        if self.max_observation_age < timedelta(0):
            raise ValueError("max_observation_age cannot be negative")
        if not self.degraded_components_allowed <= self.required_components:
            raise ValueError("degraded allowance must be limited to required components")

    def evaluate(self, report: HealthReport, *, now: datetime) -> ReadinessDecision:
        _aware(now, "now")
        observations = report.by_component()
        reasons: list[str] = []
        for component in sorted(self.required_components, key=lambda item: item.value):
            observation = observations.get(component)
            if observation is None:
                reasons.append(f"{component.value}_MISSING")
                continue
            age = now - observation.checked_at
            if age < timedelta(0):
                reasons.append(f"{component.value}_CLOCK_SKEW")
            elif age > self.max_observation_age:
                reasons.append(f"{component.value}_STALE")
            if observation.level is HealthLevel.HEALTHY:
                continue
            if (
                observation.level is HealthLevel.DEGRADED
                and component in self.degraded_components_allowed
            ):
                continue
            reasons.append(f"{component.value}_{observation.level.value}")
        return ReadinessDecision(ready=not reasons, reasons=tuple(reasons))


HealthProbe = Callable[[], Awaitable[ProbeResult]]


class HealthAggregator:
    """Runs independent probes concurrently and never exposes exception messages."""

    def __init__(
        self,
        probes: Mapping[Component, HealthProbe],
        *,
        timeout: timedelta = timedelta(seconds=3),
    ) -> None:
        if timeout <= timedelta(0):
            raise ValueError("timeout must be positive")
        self._probes = dict(probes)
        self._timeout_seconds = timeout.total_seconds()

    async def collect(self, *, now: datetime) -> HealthReport:
        _aware(now, "now")
        observations = await asyncio.gather(
            *(self._run(component, probe, now=now) for component, probe in self._probes.items())
        )
        return HealthReport(
            observations=tuple(sorted(observations, key=lambda item: item.component.value)),
            generated_at=now,
        )

    async def _run(
        self,
        component: Component,
        probe: HealthProbe,
        *,
        now: datetime,
    ) -> HealthObservation:
        started = monotonic()
        try:
            result = await asyncio.wait_for(probe(), timeout=self._timeout_seconds)
        except TimeoutError:
            result = ProbeResult(HealthLevel.UNHEALTHY, "PROBE_TIMEOUT")
        except Exception:  # health boundary must convert provider failures to safe state
            result = ProbeResult(HealthLevel.UNHEALTHY, "PROBE_FAILED")
        latency_ms = max(0, round((monotonic() - started) * 1000))
        return HealthObservation(
            component=component,
            level=result.level,
            code=result.code,
            checked_at=now,
            latency_ms=latency_ms,
        )


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
