from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.notifications.outbox import NotificationOutbox
from income_tg.portfolio.bootstrap import initialize_owner


async def test_outbox_is_idempotent_and_tracks_delivery(session: AsyncSession) -> None:
    user = await initialize_owner(session, 42, Decimal("100000"))
    outbox = NotificationOutbox(session)
    first = await outbox.enqueue(
        user_id=user.id,
        event_type="SIGNAL",
        deduplication_key="signal:1",
        payload={"text": "BTC LONG"},
        priority=10,
    )
    second = await outbox.enqueue(
        user_id=user.id,
        event_type="SIGNAL",
        deduplication_key="signal:1",
        payload={"text": "duplicate"},
        priority=10,
    )
    assert first.id == second.id
    pending = await outbox.pending()
    assert [item.id for item in pending] == [first.id]
    await outbox.mark_sent(first, 123)
    assert await outbox.pending() == []
    assert first.status == "SENT"


async def test_outbox_retries_then_fails(session: AsyncSession) -> None:
    user = await initialize_owner(session, 42, Decimal("100000"))
    outbox = NotificationOutbox(session)
    record = await outbox.enqueue(
        user_id=user.id,
        event_type="URGENT",
        deduplication_key="urgent:1",
        payload={"text": "stale data"},
    )
    for _ in range(8):
        await outbox.mark_failed(record, "network unavailable")
    assert record.status == "FAILED"
    assert record.attempts == 8
