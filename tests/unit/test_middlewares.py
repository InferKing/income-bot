from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from aiogram.enums import ChatType
from aiogram.types import Chat, Message, TelegramObject, User

from income_tg.bot.middlewares import OwnerOnlyMiddleware, _is_public_id_command


async def test_owner_middleware_rejects_unknown_user() -> None:
    called = False

    async def handler(event: TelegramObject, data: dict[str, Any]) -> str:
        nonlocal called
        called = True
        return "handled"

    middleware = OwnerOnlyMiddleware(owner_id=42)
    result = await middleware(
        handler,
        TelegramObject(),
        {"event_from_user": SimpleNamespace(id=7)},
    )
    assert result is None
    assert called is False


async def test_owner_middleware_allows_owner() -> None:
    async def handler(event: TelegramObject, data: dict[str, Any]) -> str:
        return "handled"

    middleware = OwnerOnlyMiddleware(owner_id=42)
    result = await middleware(
        handler,
        TelegramObject(),
        {"event_from_user": SimpleNamespace(id=42)},
    )
    assert result == "handled"


async def test_id_command_is_public_but_only_that_exact_command() -> None:
    calls = 0

    async def handler(event: TelegramObject, data: dict[str, Any]) -> str:
        nonlocal calls
        del event, data
        calls += 1
        return "handled"

    middleware = OwnerOnlyMiddleware(owner_id=42)
    event = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=7, type=ChatType.PRIVATE),
        from_user=User(id=7, is_bot=False, first_name="Test"),
        text="/id@test_bot",
    )
    result = await middleware(handler, event, {"event_from_user": event.from_user})

    assert result == "handled"
    assert calls == 1
    assert not _is_public_id_command(event.model_copy(update={"text": "/identity"}))
    assert not _is_public_id_command(event.model_copy(update={"text": "/portfolio"}))
