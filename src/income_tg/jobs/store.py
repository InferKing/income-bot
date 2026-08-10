from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from income_tg.jobs.models import JobLease, JobState, JobStatus


class LostJobLeaseError(RuntimeError):
    pass


class InMemoryJobStore:
    """Reference store for tests and single-process ephemeral deployments."""

    def __init__(self) -> None:
        self._states: dict[str, JobState] = {}
        self._lock = asyncio.Lock()

    async def ensure(self, name: str, first_run_at: datetime) -> JobState:
        _require_aware(first_run_at)
        async with self._lock:
            state = self._states.get(name)
            if state is None:
                state = JobState(name, JobStatus.IDLE, first_run_at)
                self._states[name] = state
            return state

    async def claim_due(
        self, name: str, now: datetime, lease_duration: timedelta
    ) -> JobLease | None:
        _require_aware(now)
        async with self._lock:
            state = self._states[name]
            lease_active = (
                state.status is JobStatus.RUNNING
                and state.lease_expires_at is not None
                and state.lease_expires_at > now
            )
            if state.next_run_at > now or lease_active:
                return None
            token = uuid4().hex
            scheduled_for = (
                state.last_scheduled_for
                if state.status in {JobStatus.RUNNING, JobStatus.FAILED}
                and state.last_scheduled_for is not None
                else state.next_run_at
            )
            lease = JobLease(name, token, scheduled_for, now, now + lease_duration)
            self._states[name] = replace(
                state,
                status=JobStatus.RUNNING,
                last_scheduled_for=scheduled_for,
                last_started_at=now,
                lease_token=token,
                lease_expires_at=lease.expires_at,
            )
            return lease

    async def succeed(
        self, lease: JobLease, finished_at: datetime, next_run_at: datetime, result: str | None
    ) -> JobState:
        _require_aware(finished_at)
        _require_aware(next_run_at)
        async with self._lock:
            state = self._owned_state(lease)
            updated = replace(
                state,
                status=JobStatus.SUCCEEDED,
                next_run_at=next_run_at,
                last_succeeded_at=finished_at,
                last_error=None,
                last_result=result,
                consecutive_failures=0,
                lease_token=None,
                lease_expires_at=None,
            )
            self._states[lease.job_name] = updated
            return updated

    async def fail(
        self, lease: JobLease, finished_at: datetime, retry_at: datetime, error: str
    ) -> JobState:
        _require_aware(finished_at)
        _require_aware(retry_at)
        async with self._lock:
            state = self._owned_state(lease)
            updated = replace(
                state,
                status=JobStatus.FAILED,
                next_run_at=retry_at,
                last_failed_at=finished_at,
                last_error=error,
                consecutive_failures=state.consecutive_failures + 1,
                lease_token=None,
                lease_expires_at=None,
            )
            self._states[lease.job_name] = updated
            return updated

    async def list_states(self) -> tuple[JobState, ...]:
        async with self._lock:
            return tuple(self._states[name] for name in sorted(self._states))

    def _owned_state(self, lease: JobLease) -> JobState:
        state = self._states[lease.job_name]
        if state.status is not JobStatus.RUNNING or state.lease_token != lease.token:
            raise LostJobLeaseError(f"job lease is no longer owned: {lease.job_name}")
        return state


class JsonJobStore(InMemoryJobStore):
    """Atomic JSON snapshot store for one scheduler process.

    Atomic replace protects persistence from torn writes. Multi-process deployments should
    provide a database implementation of ``PersistentJobStore`` with row-level CAS.
    """

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._states = self._read()

    async def ensure(self, name: str, first_run_at: datetime) -> JobState:
        state = await super().ensure(name, first_run_at)
        await self._persist()
        return state

    async def claim_due(
        self, name: str, now: datetime, lease_duration: timedelta
    ) -> JobLease | None:
        lease = await super().claim_due(name, now, lease_duration)
        if lease is not None:
            await self._persist()
        return lease

    async def succeed(
        self, lease: JobLease, finished_at: datetime, next_run_at: datetime, result: str | None
    ) -> JobState:
        state = await super().succeed(lease, finished_at, next_run_at, result)
        await self._persist()
        return state

    async def fail(
        self, lease: JobLease, finished_at: datetime, retry_at: datetime, error: str
    ) -> JobState:
        state = await super().fail(lease, finished_at, retry_at, error)
        await self._persist()
        return state

    async def _persist(self) -> None:
        payload = {name: _state_to_json(state) for name, state in sorted(self._states.items())}
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        os.replace(temporary, self.path)

    def _read(self) -> dict[str, JobState]:
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return {name: _state_from_json(item) for name, item in payload.items()}


def _state_to_json(state: JobState) -> dict[str, object]:
    payload = asdict(state)
    payload["status"] = state.status.value
    for key, value in tuple(payload.items()):
        if isinstance(value, datetime):
            payload[key] = value.isoformat()
    return payload


def _state_from_json(payload: dict[str, object]) -> JobState:
    values = dict(payload)
    values["status"] = JobStatus(str(values["status"]))
    for key in (
        "next_run_at",
        "last_scheduled_for",
        "last_started_at",
        "last_succeeded_at",
        "last_failed_at",
        "lease_expires_at",
    ):
        value = values.get(key)
        values[key] = datetime.fromisoformat(str(value)) if value is not None else None
    return JobState(**values)  # type: ignore[arg-type]


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("job timestamps must be timezone-aware")
