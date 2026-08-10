from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from income_tg.operations.health import (
    Component,
    HealthAggregator,
    HealthProbe,
    HealthReport,
    ReadinessDecision,
    ReadinessPolicy,
)
from income_tg.operations.repository import (
    DatabaseProbe,
    ModelArtifactProbe,
    OperationsRepository,
    StoredHeartbeatProbe,
)
from income_tg.storage.database import Database


@dataclass(frozen=True, slots=True)
class HealthCliConfig:
    database_url: str = field(repr=False)
    model_pointer: Path
    snapshot_path: Path
    instance_id: str
    maximum_heartbeat_age: timedelta = timedelta(seconds=30)
    probe_timeout: timedelta = timedelta(seconds=3)


class ReadinessSnapshotWriter:
    """Publishes only safe health fields via same-directory atomic replacement."""

    def __init__(self, destination: Path) -> None:
        if not destination.is_absolute():
            raise ValueError("snapshot destination must be absolute")
        resolved = destination.resolve(strict=False)
        if resolved.parent == Path(resolved.anchor):
            raise ValueError("snapshot destination cannot be in filesystem root")
        if resolved.suffix != ".json":
            raise ValueError("snapshot destination must use .json suffix")
        if not resolved.parent.is_dir():
            raise ValueError("snapshot parent directory must exist")
        self._destination = resolved

    @property
    def destination(self) -> Path:
        return self._destination

    def write(self, report: HealthReport, decision: ReadinessDecision) -> None:
        payload = readiness_snapshot_payload(report, decision)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self._destination.name}.",
            suffix=".tmp",
            dir=self._destination.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._destination)
        finally:
            if temporary.exists():
                temporary.unlink()


def readiness_snapshot_payload(
    report: HealthReport,
    decision: ReadinessDecision,
) -> dict[str, object]:
    return {
        "generated_at": report.generated_at.isoformat(),
        "level": report.level.value,
        "ready": decision.ready,
        "reasons": list(decision.reasons),
        "components": [
            {
                "component": item.component.value,
                "level": item.level.value,
                "code": item.code,
                "checked_at": item.checked_at.isoformat(),
                "latency_ms": item.latency_ms,
            }
            for item in report.observations
        ],
    }


async def run_once(config: HealthCliConfig) -> ReadinessDecision:
    now = datetime.now(UTC)
    writer = ReadinessSnapshotWriter(config.snapshot_path)
    database = Database(config.database_url)
    try:
        async with database.session() as session:
            repository = OperationsRepository(session)
            probes: dict[Component, HealthProbe] = {
                Component.DATABASE: DatabaseProbe(database.session_factory),
                Component.MARKET: StoredHeartbeatProbe(
                    database.session_factory,
                    Component.MARKET,
                    maximum_age=config.maximum_heartbeat_age,
                    clock=lambda: now,
                ),
                Component.MODEL: ModelArtifactProbe(config.model_pointer),
                Component.BOT: StoredHeartbeatProbe(
                    database.session_factory,
                    Component.BOT,
                    maximum_age=config.maximum_heartbeat_age,
                    clock=lambda: now,
                ),
            }
            report = await HealthAggregator(probes, timeout=config.probe_timeout).collect(now=now)
            for component in (Component.DATABASE, Component.MODEL):
                observation = report.by_component()[component]
                await repository.upsert_heartbeat(
                    component=component,
                    instance_id=config.instance_id,
                    level=observation.level,
                    code=observation.code,
                    heartbeat_at=now,
                )
        decision = ReadinessPolicy(max_observation_age=config.maximum_heartbeat_age).evaluate(
            report,
            now=now,
        )
        writer.write(report, decision)
        return decision
    finally:
        await database.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish one safe readiness snapshot")
    parser.add_argument("--database-url-env", default="INCOME_TG_DATABASE_URL")
    parser.add_argument("--model-pointer", type=Path, required=True)
    parser.add_argument("--snapshot-path", type=Path, required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--maximum-age-seconds", type=int, default=30)
    parser.add_argument("--timeout-seconds", type=int, default=3)
    parser.add_argument("--watch-seconds", type=int, default=0)
    return parser.parse_args()


async def run_watch(config: HealthCliConfig, interval_seconds: int) -> None:
    if interval_seconds <= 0:
        raise ValueError("watch interval must be positive")
    while True:
        try:
            await run_once(config)
        except Exception:
            # Keep the sidecar alive so transient database/model failures can recover.
            print("readiness_snapshot=failed code=CONFIGURATION_OR_IO_ERROR")
        await asyncio.sleep(interval_seconds)


def main() -> int:
    args = parse_args()
    database_url = os.environ.get(str(args.database_url_env))
    if not database_url:
        print("readiness_snapshot=failed code=DATABASE_DSN_MISSING")
        return 2
    try:
        config = HealthCliConfig(
            database_url=database_url,
            model_pointer=args.model_pointer,
            snapshot_path=args.snapshot_path,
            instance_id=str(args.instance_id),
            maximum_heartbeat_age=timedelta(seconds=int(args.maximum_age_seconds)),
            probe_timeout=timedelta(seconds=int(args.timeout_seconds)),
        )
        if int(args.watch_seconds) > 0:
            asyncio.run(run_watch(config, int(args.watch_seconds)))
            return 0
        decision = asyncio.run(run_once(config))
    except Exception:  # CLI boundary must not print exception text that could contain a DSN
        print("readiness_snapshot=failed code=CONFIGURATION_OR_IO_ERROR")
        return 2
    print(f"readiness_snapshot={'ready' if decision.ready else 'not_ready'}")
    return 0 if decision.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
