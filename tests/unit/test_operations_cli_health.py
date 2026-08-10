import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from income_tg.operations.cli_health import (
    HealthCliConfig,
    ReadinessSnapshotWriter,
    readiness_snapshot_payload,
    run_once,
)
from income_tg.operations.health import (
    Component,
    HealthLevel,
    HealthObservation,
    HealthReport,
    ReadinessDecision,
)
from income_tg.operations.repository import OperationsRepository
from income_tg.storage import trading_models as trading_models
from income_tg.storage.database import Database
from income_tg.storage.models import Base

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def safe_report() -> HealthReport:
    return HealthReport(
        observations=tuple(
            HealthObservation(
                component=component,
                level=HealthLevel.HEALTHY,
                code=f"{component.value}_OK",
                checked_at=NOW,
                latency_ms=1,
            )
            for component in Component
        ),
        generated_at=NOW,
    )


def test_snapshot_writer_atomically_replaces_existing_safe_json(tmp_path: Path) -> None:
    destination = (tmp_path / "readiness.json").resolve()
    destination.write_text("old-content", encoding="utf-8")
    report = safe_report()
    decision = ReadinessDecision(ready=True, reasons=())

    ReadinessSnapshotWriter(destination).write(report, decision)

    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload == readiness_snapshot_payload(report, decision)
    assert payload["ready"] is True
    assert not list(tmp_path.glob(".readiness.json.*.tmp"))


def test_snapshot_contains_no_arbitrary_details_or_secrets(tmp_path: Path) -> None:
    secret = "postgresql://user:secret-password@db/income_tg"
    destination = (tmp_path / "readiness.json").resolve()

    ReadinessSnapshotWriter(destination).write(
        safe_report(),
        ReadinessDecision(ready=True, reasons=()),
    )

    rendered = destination.read_text(encoding="utf-8")
    assert secret not in rendered
    assert set(json.loads(rendered)) == {
        "generated_at",
        "level",
        "ready",
        "reasons",
        "components",
    }


@pytest.mark.parametrize(
    "destination",
    [
        Path("readiness.json"),
        Path("C:/readiness.json") if Path.cwd().drive else Path("/readiness.json"),
    ],
)
def test_snapshot_requires_explicit_non_root_destination(destination: Path) -> None:
    with pytest.raises(ValueError):
        ReadinessSnapshotWriter(destination)


@pytest.mark.asyncio
async def test_run_once_reads_heartbeats_probes_model_and_publishes_snapshot(
    tmp_path: Path,
) -> None:
    database_file = (tmp_path / "health.sqlite3").resolve()
    database_url = f"sqlite+aiosqlite:///{database_file.as_posix()}"
    database = Database(database_url)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime.now(UTC)
    async with database.session() as session:
        repository = OperationsRepository(session)
        for component in (Component.MARKET, Component.BOT):
            await repository.upsert_heartbeat(
                component=component,
                instance_id=f"{component.value.lower()}-1",
                level=HealthLevel.HEALTHY,
                code=f"{component.value}_OK",
                heartbeat_at=now,
            )
    await database.dispose()

    model = (tmp_path / "champion.joblib").resolve()
    model.write_bytes(b"model")
    pointer = (tmp_path / "champion.json").resolve()
    pointer.write_text(
        json.dumps(
            {
                "stage": "CHAMPION",
                "artifact_path": str(model),
                "sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    snapshot = (tmp_path / "readiness.json").resolve()

    decision = await run_once(
        HealthCliConfig(
            database_url=database_url,
            model_pointer=pointer,
            snapshot_path=snapshot,
            instance_id="readiness-1",
            maximum_heartbeat_age=timedelta(seconds=30),
        )
    )

    assert decision.ready
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    assert payload["ready"] is True
    assert {item["component"] for item in payload["components"]} == {
        item.value for item in Component
    }


def test_health_cli_config_repr_redacts_database_url(tmp_path: Path) -> None:
    secret = "postgresql+asyncpg://user:secret@db/income_tg"
    config = HealthCliConfig(
        database_url=secret,
        model_pointer=(tmp_path / "champion.json").resolve(),
        snapshot_path=(tmp_path / "readiness.json").resolve(),
        instance_id="health-1",
    )

    assert secret not in repr(config)
