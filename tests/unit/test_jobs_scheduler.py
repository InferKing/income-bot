import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from income_tg.jobs import (
    AsyncScheduler,
    InMemoryJobStore,
    JobDefinition,
    JobStatus,
    next_weekly_run,
)


@dataclass
class FakeClock:
    current: datetime
    sleeps: int = 0

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self.current += timedelta(seconds=seconds)

    def advance(self, delta: timedelta) -> None:
        self.current += delta


class RecordingHandler:
    def __init__(self, failures: int = 0) -> None:
        self.calls: list[datetime] = []
        self.failures = failures

    async def __call__(self, scheduled_for: datetime) -> str:
        self.calls.append(scheduled_for)
        if len(self.calls) <= self.failures:
            raise RuntimeError("training failed")
        return "done"


async def test_tick_claims_due_job_once_and_persists_last_run() -> None:
    now = datetime(2026, 8, 10, 3, tzinfo=UTC)
    clock = FakeClock(now)
    store = InMemoryJobStore()
    handler = RecordingHandler()
    scheduler = AsyncScheduler(
        store,
        [JobDefinition("train", timedelta(days=7), handler)],
        clock=clock,
    )

    assert await scheduler.tick() == 1
    assert await scheduler.tick() == 0
    state = (await store.list_states())[0]
    assert handler.calls == [now]
    assert state.status is JobStatus.SUCCEEDED
    assert state.last_succeeded_at == now
    assert state.last_result == "done"
    assert state.next_run_at == now + timedelta(days=7)


async def test_atomic_claim_prevents_duplicate_concurrent_execution() -> None:
    now = datetime(2026, 8, 10, tzinfo=UTC)
    store = InMemoryJobStore()
    await store.ensure("train", now)

    claims = await asyncio.gather(
        store.claim_due("train", now, timedelta(hours=1)),
        store.claim_due("train", now, timedelta(hours=1)),
    )

    assert sum(claim is not None for claim in claims) == 1


async def test_failure_retries_without_drifting_weekly_schedule() -> None:
    now = datetime(2026, 8, 10, 3, tzinfo=UTC)
    clock = FakeClock(now)
    handler = RecordingHandler(failures=1)
    store = InMemoryJobStore()
    scheduler = AsyncScheduler(
        store,
        [
            JobDefinition(
                "train",
                timedelta(days=7),
                handler,
                retry_delay=timedelta(minutes=10),
            )
        ],
        clock=clock,
    )

    await scheduler.tick()
    failed = (await store.list_states())[0]
    assert failed.status is JobStatus.FAILED
    assert failed.next_run_at == now + timedelta(minutes=10)
    clock.advance(timedelta(minutes=10))
    await scheduler.tick()
    recovered = (await store.list_states())[0]

    assert handler.calls == [now, now]
    assert recovered.status is JobStatus.SUCCEEDED
    assert recovered.next_run_at == now + timedelta(days=7)
    assert recovered.consecutive_failures == 0


async def test_health_reports_repeated_failures_without_real_sleep() -> None:
    now = datetime(2026, 8, 10, tzinfo=UTC)
    clock = FakeClock(now)
    handler = RecordingHandler(failures=3)
    store = InMemoryJobStore()
    scheduler = AsyncScheduler(
        store,
        [
            JobDefinition(
                "train",
                timedelta(days=7),
                handler,
                retry_delay=timedelta(seconds=1),
            )
        ],
        clock=clock,
        unhealthy_after_failures=3,
    )
    for _ in range(3):
        await scheduler.tick()
        clock.advance(timedelta(seconds=1))

    health = await scheduler.health()
    assert not health.healthy
    assert health.last_tick_at is not None
    assert health.problems == ("train: repeated failures",)
    assert clock.sleeps == 0


def test_next_weekly_run_is_strictly_in_the_future() -> None:
    monday_before = datetime(2026, 8, 10, 2, tzinfo=UTC)
    monday_exact = datetime(2026, 8, 10, 3, tzinfo=UTC)

    assert next_weekly_run(monday_before) == datetime(2026, 8, 10, 3, tzinfo=UTC)
    assert next_weekly_run(monday_exact) == datetime(2026, 8, 17, 3, tzinfo=UTC)
