from types import SimpleNamespace
from typing import Any

from income_tg.bot.handlers import telegram_id


class FakeMessage:
    def __init__(self, user_id: int) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        del kwargs
        self.answers.append(text)


async def test_id_command_returns_sender_id() -> None:
    message = FakeMessage(123456789)

    await telegram_id(message)  # type: ignore[arg-type]

    assert message.answers == ["Ваш Telegram ID: <code>123456789</code>"]
