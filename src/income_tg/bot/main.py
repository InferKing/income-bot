from __future__ import annotations

import asyncio

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
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
    bot = Bot(
        token=settings.require_bot_token(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(settings=settings)
    owner_middleware = OwnerOnlyMiddleware(owner_id)
    database_middleware = DatabaseSessionMiddleware(database)
    dispatcher.message.outer_middleware(owner_middleware)
    dispatcher.message.outer_middleware(database_middleware)
    dispatcher.callback_query.outer_middleware(owner_middleware)
    dispatcher.callback_query.outer_middleware(database_middleware)
    dispatcher.include_router(router)

    await bot.set_my_commands(
        [
            BotCommand(command="id", description="Показать мой Telegram ID"),
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="portfolio", description="Показать портфели"),
            BotCommand(command="signals", description="Последние сигналы"),
            BotCommand(command="stats", description="Статистика paper trading"),
            BotCommand(command="risk", description="Настройки риска"),
            BotCommand(command="status", description="Состояние системы"),
            BotCommand(command="help", description="Форматы операций"),
        ]
    )
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
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        await cancel_delivery_task(notification_task)
        await bot.session.close()
        await database.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
