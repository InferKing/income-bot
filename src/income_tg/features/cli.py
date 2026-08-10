from __future__ import annotations

import argparse
import asyncio

import structlog
from sqlalchemy import select

from income_tg.config import get_settings
from income_tg.features.service import OnlineFeatureService
from income_tg.logging import configure_logging
from income_tg.storage.database import Database
from income_tg.storage.trading_models import InstrumentRecord


async def run(*, once: bool = False) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    database = Database(settings.database_url)
    logger = structlog.get_logger()
    try:
        while True:
            async with database.session() as session:
                instruments = list(
                    await session.scalars(
                        select(InstrumentRecord).where(InstrumentRecord.is_active.is_(True))
                    )
                )
                created = 0
                service = OnlineFeatureService(session)
                for instrument in instruments:
                    created += await service.build_latest(instrument)
            logger.info("feature_cycle_completed", vectors_created=created)
            if once:
                return
            await asyncio.sleep(30)
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(once=args.once))


if __name__ == "__main__":
    main()
