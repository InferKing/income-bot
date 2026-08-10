from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from income_tg.operations.health import Component, HealthLevel, ProbeResult
from income_tg.storage.trading_models import ServiceHealthRecord

TOKEN_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,127}$")
INSTANCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class StoredHeartbeat:
    component: Component
    instance_id: str
    level: HealthLevel
    code: str
    last_heartbeat_at: datetime
    stale: bool


class OperationsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_heartbeat(
        self,
        *,
        component: Component,
        instance_id: str,
        level: HealthLevel,
        code: str,
        heartbeat_at: datetime,
    ) -> None:
        _aware(heartbeat_at, "heartbeat_at")
        if not INSTANCE_PATTERN.fullmatch(instance_id):
            raise ValueError("instance_id must be a safe machine-readable token")
        if not TOKEN_PATTERN.fullmatch(code):
            raise ValueError("code must be a safe machine-readable token")
        values = {
            "service": component.value,
            "instance_id": instance_id,
            "status": level.value,
            "last_heartbeat_at": heartbeat_at,
            "details": {"code": code},
        }
        dialect = self._session.get_bind().dialect.name
        if dialect == "postgresql":
            postgres_statement = postgres_insert(ServiceHealthRecord).values(**values)
            postgres_statement = postgres_statement.on_conflict_do_update(
                index_elements=("service", "instance_id"),
                set_={
                    "status": postgres_statement.excluded.status,
                    "last_heartbeat_at": postgres_statement.excluded.last_heartbeat_at,
                    "details": postgres_statement.excluded.details,
                },
            )
            await self._session.execute(postgres_statement)
        elif dialect == "sqlite":
            sqlite_statement = sqlite_insert(ServiceHealthRecord).values(**values)
            sqlite_statement = sqlite_statement.on_conflict_do_update(
                index_elements=("service", "instance_id"),
                set_={
                    "status": sqlite_statement.excluded.status,
                    "last_heartbeat_at": sqlite_statement.excluded.last_heartbeat_at,
                    "details": sqlite_statement.excluded.details,
                },
            )
            await self._session.execute(sqlite_statement)
        else:
            await self._session.merge(ServiceHealthRecord(**values))
        await self._session.flush()

    async def read_latest(
        self,
        component: Component,
        *,
        now: datetime,
        maximum_age: timedelta,
    ) -> StoredHeartbeat | None:
        _aware(now, "now")
        if maximum_age < timedelta(0):
            raise ValueError("maximum_age cannot be negative")
        statement = (
            select(ServiceHealthRecord)
            .where(ServiceHealthRecord.service == component.value)
            .order_by(ServiceHealthRecord.last_heartbeat_at.desc())
            .limit(1)
        )
        record = await self._session.scalar(statement)
        if record is None:
            return None
        heartbeat_at = _as_utc(record.last_heartbeat_at)
        try:
            level = HealthLevel(record.status)
        except ValueError:
            level = HealthLevel.UNKNOWN
        raw_code = record.details.get("code")
        code = (
            raw_code
            if isinstance(raw_code, str) and TOKEN_PATTERN.fullmatch(raw_code)
            else "UNKNOWN"
        )
        age = now - heartbeat_at
        return StoredHeartbeat(
            component=component,
            instance_id=record.instance_id,
            level=level,
            code=code,
            last_heartbeat_at=heartbeat_at,
            stale=age < timedelta(0) or age > maximum_age,
        )


class DatabaseProbe:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __call__(self) -> ProbeResult:
        try:
            async with self._session_factory() as session:
                await session.execute(text("SELECT 1"))
        except Exception:  # probe boundary deliberately returns a safe code
            return ProbeResult(HealthLevel.UNHEALTHY, "DATABASE_UNAVAILABLE")
        return ProbeResult(HealthLevel.HEALTHY, "DATABASE_OK")


class StoredHeartbeatProbe:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        component: Component,
        *,
        maximum_age: timedelta,
        clock: Callable[[], datetime],
    ) -> None:
        self._session_factory = session_factory
        self._component = component
        self._maximum_age = maximum_age
        self._clock = clock

    async def __call__(self) -> ProbeResult:
        try:
            async with self._session_factory() as session:
                heartbeat = await OperationsRepository(session).read_latest(
                    self._component,
                    now=self._clock(),
                    maximum_age=self._maximum_age,
                )
        except Exception:  # repository probe boundary deliberately returns a safe code
            return ProbeResult(HealthLevel.UNHEALTHY, "HEARTBEAT_READ_FAILED")
        if heartbeat is None:
            return ProbeResult(HealthLevel.UNHEALTHY, "HEARTBEAT_MISSING")
        if heartbeat.stale:
            return ProbeResult(HealthLevel.UNHEALTHY, "HEARTBEAT_STALE")
        return ProbeResult(heartbeat.level, heartbeat.code)


class ModelArtifactProbe:
    """Validates a champion pointer and artifact without deserializing model code."""

    def __init__(self, champion_pointer: Path) -> None:
        if not champion_pointer.is_absolute():
            raise ValueError("champion_pointer must be absolute")
        pointer = champion_pointer.resolve(strict=False)
        if pointer.parent == Path(pointer.anchor):
            raise ValueError("champion_pointer cannot be placed in filesystem root")
        self._pointer = pointer

    async def __call__(self) -> ProbeResult:
        if not self._pointer.is_file():
            return ProbeResult(HealthLevel.UNHEALTHY, "MODEL_POINTER_MISSING")
        try:
            payload = json.loads(self._pointer.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return ProbeResult(HealthLevel.UNHEALTHY, "MODEL_POINTER_INVALID")
        if not isinstance(payload, dict) or payload.get("stage") != "CHAMPION":
            return ProbeResult(HealthLevel.UNHEALTHY, "MODEL_NOT_CHAMPION")
        raw_artifact = payload.get("artifact_path")
        expected_digest = payload.get("sha256")
        if not isinstance(raw_artifact, str) or not isinstance(expected_digest, str):
            return ProbeResult(HealthLevel.UNHEALTHY, "MODEL_METADATA_INVALID")
        artifact = Path(raw_artifact)
        if not artifact.is_absolute():
            artifact = self._pointer.parent / artifact
        artifact = artifact.resolve(strict=False)
        if not artifact.is_relative_to(self._pointer.parent):
            return ProbeResult(HealthLevel.UNHEALTHY, "MODEL_ARTIFACT_OUTSIDE_ROOT")
        if not artifact.is_file():
            return ProbeResult(HealthLevel.UNHEALTHY, "MODEL_ARTIFACT_MISSING")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_digest):
            return ProbeResult(HealthLevel.UNHEALTHY, "MODEL_METADATA_INVALID")
        try:
            actual_digest = _sha256(artifact)
        except OSError:
            return ProbeResult(HealthLevel.UNHEALTHY, "MODEL_ARTIFACT_UNREADABLE")
        if actual_digest != expected_digest.lower():
            return ProbeResult(HealthLevel.UNHEALTHY, "MODEL_CHECKSUM_MISMATCH")
        return ProbeResult(HealthLevel.HEALTHY, "MODEL_OK")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
