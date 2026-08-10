from __future__ import annotations

from datetime import timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.common.time import utc_now
from income_tg.storage.trading_models import NotificationOutboxRecord


class NotificationOutbox:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enqueue(
        self,
        *,
        user_id: UUID,
        event_type: str,
        deduplication_key: str,
        payload: dict[str, Any],
        priority: int = 0,
    ) -> NotificationOutboxRecord:
        identifier = uuid4()
        values = {
            "id": identifier,
            "user_id": user_id,
            "event_type": event_type,
            "deduplication_key": deduplication_key,
            "priority": priority,
            "payload": payload,
            "status": "PENDING",
            "attempts": 0,
            "next_attempt_at": utc_now(),
        }
        dialect = self.session.get_bind().dialect.name
        table = cast(Any, NotificationOutboxRecord.__table__)
        statement: Any
        if dialect == "postgresql":
            statement = postgresql_insert(table).values(**values)
        elif dialect == "sqlite":
            statement = sqlite_insert(table).values(**values)
        else:
            raise RuntimeError(f"unsupported SQL dialect: {dialect}")
        result = await self.session.execute(
            statement.on_conflict_do_nothing(index_elements=["deduplication_key"])
        )
        if bool(getattr(result, "rowcount", 0)):
            record = await self.session.get(NotificationOutboxRecord, identifier)
            if record is None:
                raise RuntimeError("inserted notification could not be reloaded")
            return record
        existing = await self.session.scalar(
            select(NotificationOutboxRecord).where(
                NotificationOutboxRecord.deduplication_key == deduplication_key
            )
        )
        if existing is None:
            raise RuntimeError("notification conflict did not return existing row")
        return existing

    async def pending(self, *, limit: int = 20) -> list[NotificationOutboxRecord]:
        now = utc_now()
        statement = (
            select(NotificationOutboxRecord)
            .where(
                NotificationOutboxRecord.status == "PENDING",
                NotificationOutboxRecord.next_attempt_at <= now,
            )
            .order_by(
                NotificationOutboxRecord.priority.desc(),
                NotificationOutboxRecord.created_at,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(await self.session.scalars(statement))

    async def mark_sent(self, record: NotificationOutboxRecord, telegram_message_id: int) -> None:
        record.status = "SENT"
        record.telegram_message_id = telegram_message_id
        record.sent_at = utc_now()
        record.last_error = None
        await self.session.flush()

    async def mark_failed(self, record: NotificationOutboxRecord, error: str) -> None:
        record.attempts += 1
        record.last_error = error[:2_000]
        if record.attempts >= 8:
            record.status = "FAILED"
        else:
            delay_seconds = min(2**record.attempts, 300)
            record.next_attempt_at = utc_now() + timedelta(seconds=delay_seconds)
        await self.session.flush()
