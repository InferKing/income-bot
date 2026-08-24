from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from income_tg.jobs.models import JobDefinition
from income_tg.storage.trading_models import OrderbookSnapshotRecord


class OrderbookRetentionJob:
    """Deletes expired raw order-book snapshots in short committed batches."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        retention: timedelta = timedelta(days=7),
        batch_size: int = 10_000,
        max_batches: int = 100,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if retention <= timedelta(0):
            raise ValueError("retention must be positive")
        if batch_size <= 0:
            raise ValueError("batch size must be positive")
        if max_batches <= 0:
            raise ValueError("maximum batch count must be positive")
        self._session_factory = session_factory
        self._retention = retention
        self._batch_size = batch_size
        self._max_batches = max_batches
        self._clock = clock or (lambda: datetime.now(UTC))

    async def __call__(self, scheduled_for: datetime) -> str:
        del scheduled_for
        cutoff = self._clock() - self._retention
        _aware(cutoff, "retention cutoff")
        deleted_total = 0
        batches = 0
        for _ in range(self._max_batches):
            async with self._session_factory() as session, session.begin():
                deleted = await delete_expired_orderbook_batch(
                    session,
                    cutoff=cutoff,
                    batch_size=self._batch_size,
                )
            deleted_total += deleted
            batches += 1
            if deleted < self._batch_size:
                break
        structlog.get_logger().info(
            "orderbook_retention_completed",
            cutoff=cutoff.isoformat(),
            deleted=deleted_total,
            batches=batches,
        )
        return f"deleted={deleted_total} cutoff={cutoff.isoformat()}"


async def delete_expired_orderbook_batch(
    session: AsyncSession,
    *,
    cutoff: datetime,
    batch_size: int = 10_000,
) -> int:
    _aware(cutoff, "retention cutoff")
    if batch_size <= 0:
        raise ValueError("batch size must be positive")
    oldest_captured_at = await session.scalar(
        select(OrderbookSnapshotRecord.captured_at).order_by(OrderbookSnapshotRecord.id).limit(1)
    )
    if oldest_captured_at is None or _as_utc(oldest_captured_at) >= cutoff:
        return 0
    expired_ids = (
        select(OrderbookSnapshotRecord.id)
        .where(OrderbookSnapshotRecord.captured_at < cutoff)
        .order_by(OrderbookSnapshotRecord.id)
        .limit(batch_size)
    )
    result = await session.execute(
        delete(OrderbookSnapshotRecord)
        .where(OrderbookSnapshotRecord.id.in_(expired_ids))
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)


def orderbook_retention_definition(
    job: OrderbookRetentionJob,
    *,
    interval: timedelta = timedelta(hours=1),
) -> JobDefinition:
    return JobDefinition(
        name="orderbook-retention",
        interval=interval,
        handler=job,
        retry_delay=timedelta(minutes=15),
        lease_duration=timedelta(hours=2),
    )


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
