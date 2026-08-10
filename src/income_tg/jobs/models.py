from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol


class JobStatus(StrEnum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class JobDefinition:
    name: str
    interval: timedelta
    handler: JobHandler
    initial_delay: timedelta = timedelta(0)
    retry_delay: timedelta = timedelta(minutes=15)
    lease_duration: timedelta = timedelta(hours=6)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("job name must not be empty")
        if self.interval <= timedelta(0):
            raise ValueError("job interval must be positive")
        if self.initial_delay < timedelta(0):
            raise ValueError("initial delay must be non-negative")
        if self.retry_delay <= timedelta(0):
            raise ValueError("retry delay must be positive")
        if self.lease_duration <= timedelta(0):
            raise ValueError("lease duration must be positive")


class JobHandler(Protocol):
    async def __call__(self, scheduled_for: datetime) -> str | None:
        """Run one occurrence and optionally return a short persistent result summary."""


@dataclass(frozen=True, slots=True)
class JobLease:
    job_name: str
    token: str
    scheduled_for: datetime
    started_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class JobState:
    name: str
    status: JobStatus
    next_run_at: datetime
    last_scheduled_for: datetime | None = None
    last_started_at: datetime | None = None
    last_succeeded_at: datetime | None = None
    last_failed_at: datetime | None = None
    last_error: str | None = None
    last_result: str | None = None
    consecutive_failures: int = 0
    lease_token: str | None = None
    lease_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SchedulerHealth:
    healthy: bool
    running: bool
    checked_at: datetime
    last_tick_at: datetime | None
    jobs: tuple[JobState, ...]
    problems: tuple[str, ...]


class PersistentJobStore(Protocol):
    """Durable compare-and-set boundary used to prevent duplicate job execution."""

    async def ensure(self, name: str, first_run_at: datetime) -> JobState:
        """Create an idle state if it does not exist."""

    async def claim_due(
        self, name: str, now: datetime, lease_duration: timedelta
    ) -> JobLease | None:
        """Atomically claim a due occurrence, including an expired prior lease."""

    async def succeed(
        self, lease: JobLease, finished_at: datetime, next_run_at: datetime, result: str | None
    ) -> JobState:
        """Complete only if the lease token still owns the occurrence."""

    async def fail(
        self, lease: JobLease, finished_at: datetime, retry_at: datetime, error: str
    ) -> JobState:
        """Fail only if the lease token still owns the occurrence."""

    async def list_states(self) -> tuple[JobState, ...]:
        """Return an immutable snapshot ordered by job name."""
