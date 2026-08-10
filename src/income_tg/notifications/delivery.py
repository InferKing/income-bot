from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

import structlog
from aiogram import Bot

from income_tg.notifications.outbox import NotificationOutbox
from income_tg.storage.database import Database


async def run_delivery_loop(
    *,
    bot: Bot,
    database: Database,
    telegram_owner_id: int,
    poll_interval: float = 2.0,
) -> None:
    logger = structlog.get_logger()
    while True:
        try:
            async with database.session() as session:
                outbox = NotificationOutbox(session)
                for record in await outbox.pending():
                    try:
                        message = await bot.send_message(
                            telegram_owner_id,
                            _render_payload(record.payload),
                        )
                        await outbox.mark_sent(record, message.message_id)
                    except Exception as error:
                        await outbox.mark_failed(record, str(error))
                        logger.warning(
                            "notification_delivery_failed",
                            notification_id=str(record.id),
                            error_type=type(error).__name__,
                        )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("notification_loop_failed")
        await asyncio.sleep(poll_interval)


def _render_payload(payload: dict[str, Any]) -> str:
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Notification payload requires non-empty text")
    return text


async def cancel_delivery_task(task: asyncio.Task[None]) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
