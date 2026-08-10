from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog

from income_tg.operations.health import Component, HealthLevel
from income_tg.operations.repository import OperationsRepository
from income_tg.storage.database import Database


async def run_heartbeat_loop(
    database: Database,
    component: Component,
    instance_id: str,
    *,
    interval_seconds: float = 10.0,
) -> None:
    logger = structlog.get_logger()
    while True:
        try:
            async with database.session() as session:
                await OperationsRepository(session).upsert_heartbeat(
                    component=component,
                    instance_id=instance_id,
                    level=HealthLevel.HEALTHY,
                    code="RUNNING",
                    heartbeat_at=datetime.now(UTC),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "service_heartbeat_failed",
                component=component.value,
                instance_id=instance_id,
            )
        await asyncio.sleep(interval_seconds)
