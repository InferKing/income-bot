from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

from income_tg.jobs.models import (
    JobDefinition,
    JobStatus,
    PersistentJobStore,
    SchedulerHealth,
)


class Clock(Protocol):
    def now(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class AsyncScheduler:
    def __init__(
        self,
        store: PersistentJobStore,
        jobs: Sequence[JobDefinition],
        *,
        clock: Clock | None = None,
        poll_interval: timedelta = timedelta(seconds=30),
        unhealthy_after_failures: int = 3,
    ) -> None:
        if poll_interval <= timedelta(0):
            raise ValueError("poll interval must be positive")
        if unhealthy_after_failures <= 0:
            raise ValueError("unhealthy failure threshold must be positive")
        names = [job.name for job in jobs]
        if len(set(names)) != len(names):
            raise ValueError("job names must be unique")
        self.store = store
        self.jobs = tuple(jobs)
        self.clock = clock or SystemClock()
        self.poll_interval = poll_interval
        self.unhealthy_after_failures = unhealthy_after_failures
        self._initialized = False
        self._running = False
        self._last_tick_at: datetime | None = None

    async def initialize(self) -> None:
        if self._initialized:
            return
        now = self.clock.now()
        for job in self.jobs:
            await self.store.ensure(job.name, now + job.initial_delay)
        self._initialized = True

    async def tick(self) -> int:
        await self.initialize()
        executed = 0
        for job in self.jobs:
            now = self.clock.now()
            lease = await self.store.claim_due(job.name, now, job.lease_duration)
            if lease is None:
                continue
            executed += 1
            try:
                result = await job.handler(lease.scheduled_for)
            except Exception as error:
                finished_at = self.clock.now()
                await self.store.fail(
                    lease,
                    finished_at,
                    finished_at + job.retry_delay,
                    _safe_error(error),
                )
            else:
                finished_at = self.clock.now()
                next_run_at = _next_occurrence(lease.scheduled_for, job.interval, finished_at)
                await self.store.succeed(lease, finished_at, next_run_at, result)
        self._last_tick_at = self.clock.now()
        return executed

    async def serve(self, stop: asyncio.Event) -> None:
        self._running = True
        try:
            while not stop.is_set():
                await self.tick()
                if stop.is_set():
                    break
                await self.clock.sleep(self.poll_interval.total_seconds())
        finally:
            self._running = False

    async def health(self) -> SchedulerHealth:
        states = await self.store.list_states()
        now = self.clock.now()
        problems: list[str] = []
        for state in states:
            if state.consecutive_failures >= self.unhealthy_after_failures:
                problems.append(f"{state.name}: repeated failures")
            if (
                state.status is JobStatus.RUNNING
                and state.lease_expires_at is not None
                and state.lease_expires_at <= now
            ):
                problems.append(f"{state.name}: expired lease")
        return SchedulerHealth(
            healthy=not problems,
            running=self._running,
            checked_at=now,
            last_tick_at=self._last_tick_at,
            jobs=states,
            problems=tuple(problems),
        )


def next_weekly_run(now: datetime, *, weekday: int = 0, hour: int = 3) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if not 0 <= weekday <= 6 or not 0 <= hour <= 23:
        raise ValueError("weekday or hour is out of range")
    days = (weekday - now.weekday()) % 7
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=days)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def _next_occurrence(scheduled_for: datetime, interval: timedelta, after: datetime) -> datetime:
    next_run = scheduled_for + interval
    while next_run <= after:
        next_run += interval
    return next_run


def _safe_error(error: Exception) -> str:
    message = f"{type(error).__name__}: {error}"
    return message[:1000]
