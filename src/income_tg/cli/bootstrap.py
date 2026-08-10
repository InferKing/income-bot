from __future__ import annotations

import asyncio

import structlog

from income_tg.config import get_settings
from income_tg.logging import configure_logging
from income_tg.portfolio.bootstrap import initialize_owner
from income_tg.storage.database import Database


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    owner_id = settings.require_owner_id()
    database = Database(settings.database_url)
    try:
        async with database.session() as session:
            user = await initialize_owner(
                session,
                telegram_user_id=owner_id,
                initial_paper_balance_rub=settings.initial_paper_balance_rub,
            )
        structlog.get_logger().info("owner_initialized", user_id=str(user.id))
    finally:
        await database.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
