from __future__ import annotations

import asyncio

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import BotCommand

from income_tg.bot.handlers import router
from income_tg.bot.middlewares import DatabaseSessionMiddleware, OwnerOnlyMiddleware
from income_tg.config import get_settings
from income_tg.logging import configure_logging
from income_tg.notifications.delivery import cancel_delivery_task, run_delivery_loop
from income_tg.operations.health import Component
from income_tg.operations.heartbeat import run_heartbeat_loop
from income_tg.storage.database import Database


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    owner_id = settings.require_owner_id()
    database = Database(settings.database_url)
    proxy_url = (
        settings.telegram_proxy_url.get_secret_value()
        if settings.telegram_proxy_url is not None
        else None
    )
    bot = Bot(
        token=settings.require_bot_token(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=AiohttpSession(proxy=proxy_url),
    )
    dispatcher = Dispatcher(settings=settings)
    owner_middleware = OwnerOnlyMiddleware(owner_id)
    database_middleware = DatabaseSessionMiddleware(database)
    dispatcher.message.outer_middleware(owner_middleware)
    dispatcher.message.outer_middleware(database_middleware)
    dispatcher.callback_query.outer_middleware(owner_middleware)
    dispatcher.callback_query.outer_middleware(database_middleware)
    dispatcher.include_router(router)

    notification_task: asyncio.Task[None] | None = None
    heartbeat_task: asyncio.Task[None] | None = None
    try:
        commands = [
            BotCommand(command="id", description="Показать мой Telegram ID"),
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="portfolio", description="Показать портфели"),
            BotCommand(command="signals", description="Последние сигналы"),
            BotCommand(command="stats", description="Статистика paper trading"),
            BotCommand(command="risk", description="Настройки риска"),
            BotCommand(command="status", description="Состояние системы"),
            BotCommand(command="help", description="Форматы операций"),
        ]
        while True:
            try:
                await bot.set_my_commands(commands)
                break
            except TelegramNetworkError:
                structlog.get_logger().warning(
                    "telegram_startup_retry",
                    retry_in_seconds=30,
                    exc_info=True,
                )
                await asyncio.sleep(30)
        structlog.get_logger().info("bot_started", owner_id=owner_id)
        notification_task = asyncio.create_task(
            run_delivery_loop(
                bot=bot,
                database=database,
                telegram_owner_id=owner_id,
            ),
            name="notification-delivery",
        )
        heartbeat_task = asyncio.create_task(
            run_heartbeat_loop(database, Component.BOT, "telegram-bot-1"),
            name="bot-heartbeat",
        )
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        if notification_task is not None:
            await cancel_delivery_task(notification_task)
        await bot.session.close()
        await database.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
