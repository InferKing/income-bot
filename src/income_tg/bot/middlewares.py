from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from income_tg.storage.database import Database

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class OwnerOnlyMiddleware(BaseMiddleware):
    def __init__(self, owner_id: int) -> None:
        self.owner_id = owner_id

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if _is_public_id_command(event):
            return await handler(event, data)
        if user is None or user.id != self.owner_id:
            if isinstance(event, Message):
                await event.answer("Доступ запрещен.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Доступ запрещен.", show_alert=True)
            return None
        return await handler(event, data)


def _is_public_id_command(event: TelegramObject) -> bool:
    if not isinstance(event, Message) or not event.text:
        return False
    command = event.text.strip().split(maxsplit=1)[0].casefold()
    return command.split("@", maxsplit=1)[0] == "/id"


class DatabaseSessionMiddleware(BaseMiddleware):
    def __init__(self, database: Database) -> None:
        self.database = database

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.database.session() as session:
            data["session"] = session
            return await handler(event, data)
