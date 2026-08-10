import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from income_tg.operations.health import Component, HealthLevel
from income_tg.operations.repository import (
    DatabaseProbe,
    ModelArtifactProbe,
    OperationsRepository,
    StoredHeartbeatProbe,
)
from income_tg.storage.trading_models import ServiceHealthRecord

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_heartbeat_upsert_updates_single_record(session: AsyncSession) -> None:
    repository = OperationsRepository(session)
    await repository.upsert_heartbeat(
        component=Component.BOT,
        instance_id="bot-1",
        level=HealthLevel.HEALTHY,
        code="BOT_OK",
        heartbeat_at=NOW,
    )
    await repository.upsert_heartbeat(
        component=Component.BOT,
        instance_id="bot-1",
        level=HealthLevel.DEGRADED,
        code="BOT_RATE_LIMITED",
        heartbeat_at=NOW + timedelta(seconds=1),
    )

    count = await session.scalar(select(func.count()).select_from(ServiceHealthRecord))
    record = await session.get(ServiceHealthRecord, (Component.BOT.value, "bot-1"))
    assert count == 1
    assert record is not None
    assert record.status == HealthLevel.DEGRADED.value
    assert record.details == {"code": "BOT_RATE_LIMITED"}
    assert "secret" not in repr(record.details)


@pytest.mark.asyncio
async def test_read_latest_selects_newest_instance_and_evaluates_staleness(
    session: AsyncSession,
) -> None:
    repository = OperationsRepository(session)
    await repository.upsert_heartbeat(
        component=Component.MARKET,
        instance_id="collector-old",
        level=HealthLevel.HEALTHY,
        code="MARKET_OK",
        heartbeat_at=NOW - timedelta(seconds=60),
    )
    await repository.upsert_heartbeat(
        component=Component.MARKET,
        instance_id="collector-new",
        level=HealthLevel.DEGRADED,
        code="MARKET_BACKFILLING",
        heartbeat_at=NOW - timedelta(seconds=5),
    )

    fresh = await repository.read_latest(
        Component.MARKET,
        now=NOW,
        maximum_age=timedelta(seconds=30),
    )
    stale = await repository.read_latest(
        Component.MARKET,
        now=NOW + timedelta(seconds=31),
        maximum_age=timedelta(seconds=30),
    )

    assert fresh is not None
    assert fresh.instance_id == "collector-new"
    assert fresh.level is HealthLevel.DEGRADED
    assert not fresh.stale
    assert stale is not None and stale.stale


@pytest.mark.asyncio
async def test_missing_heartbeat_returns_none(session: AsyncSession) -> None:
    assert (
        await OperationsRepository(session).read_latest(
            Component.MODEL,
            now=NOW,
            maximum_age=timedelta(seconds=30),
        )
        is None
    )


@pytest.mark.asyncio
async def test_database_probe_reports_success_and_sanitized_failure() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    assert (await DatabaseProbe(factory)()).code == "DATABASE_OK"
    await engine.dispose()

    class BrokenFactory:
        def __call__(self) -> object:
            raise RuntimeError("postgresql://user:secret@database/income_tg")

    broken = cast(async_sessionmaker[AsyncSession], BrokenFactory())
    failure = await DatabaseProbe(broken)()
    assert failure.level is HealthLevel.UNHEALTHY
    assert failure.code == "DATABASE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_stored_heartbeat_probe_maps_missing_stale_and_current(
    session: AsyncSession,
) -> None:
    factory = async_sessionmaker(
        bind=session.bind,
        expire_on_commit=False,
    )
    repository = OperationsRepository(session)
    missing_probe = StoredHeartbeatProbe(
        factory,
        Component.BOT,
        maximum_age=timedelta(seconds=30),
        clock=lambda: NOW,
    )
    assert (await missing_probe()).code == "HEARTBEAT_MISSING"

    await repository.upsert_heartbeat(
        component=Component.BOT,
        instance_id="bot-1",
        level=HealthLevel.HEALTHY,
        code="BOT_OK",
        heartbeat_at=NOW,
    )
    await session.commit()
    current = await missing_probe()
    stale_probe = StoredHeartbeatProbe(
        factory,
        Component.BOT,
        maximum_age=timedelta(seconds=30),
        clock=lambda: NOW + timedelta(seconds=31),
    )

    assert current == current.__class__(HealthLevel.HEALTHY, "BOT_OK")
    assert (await stale_probe()).code == "HEARTBEAT_STALE"


def create_model_files(root: Path) -> tuple[Path, Path]:
    artifact = (root / "model.joblib").resolve()
    artifact.write_bytes(b"deterministic-model")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    pointer = (root / "champion.json").resolve()
    pointer.write_text(
        json.dumps(
            {
                "stage": "CHAMPION",
                "artifact_path": str(artifact),
                "sha256": digest,
            }
        ),
        encoding="utf-8",
    )
    return pointer, artifact


@pytest.mark.asyncio
async def test_model_artifact_probe_validates_pointer_stage_path_and_checksum(
    tmp_path: Path,
) -> None:
    pointer, artifact = create_model_files(tmp_path)
    probe = ModelArtifactProbe(pointer)

    assert (await probe()).code == "MODEL_OK"
    artifact.write_bytes(b"tampered")
    assert (await probe()).code == "MODEL_CHECKSUM_MISMATCH"


@pytest.mark.asyncio
async def test_model_probe_rejects_artifact_outside_registry_root(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    registry.mkdir()
    outside = (tmp_path / "outside.joblib").resolve()
    outside.write_bytes(b"model")
    pointer = (registry / "champion.json").resolve()
    pointer.write_text(
        json.dumps(
            {
                "stage": "CHAMPION",
                "artifact_path": str(outside),
                "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    assert (await ModelArtifactProbe(pointer)()).code == "MODEL_ARTIFACT_OUTSIDE_ROOT"


@pytest.mark.parametrize(
    ("instance_id", "code"),
    [("unsafe instance", "OK"), ("safe", "secret=value")],
)
@pytest.mark.asyncio
async def test_heartbeat_rejects_unsafe_free_text(
    session: AsyncSession,
    instance_id: str,
    code: str,
) -> None:
    with pytest.raises(ValueError, match="machine-readable"):
        await OperationsRepository(session).upsert_heartbeat(
            component=Component.BOT,
            instance_id=instance_id,
            level=HealthLevel.HEALTHY,
            code=code,
            heartbeat_at=NOW,
        )
