"""Run the market collector with ``python -m income_tg.market_data.cli``."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

import aiohttp
import structlog

from income_tg.config import get_settings
from income_tg.market_data.adapters.bybit import BybitAdapter
from income_tg.market_data.adapters.fx import CoinGeckoFxAdapter
from income_tg.market_data.adapters.okx import OkxAdapter
from income_tg.market_data.collector import MarketCollector
from income_tg.market_data.repository import MarketDataRepository
from income_tg.market_data.schemas import Instrument, InstrumentKind
from income_tg.market_data.transport import AioHttpRestTransport, AioHttpWebSocketTransport
from income_tg.operations.health import Component
from income_tg.operations.heartbeat import run_heartbeat_loop
from income_tg.storage.database import Database


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Income TG market-data collector")
    parser.add_argument("--provider", choices=("bybit", "okx"), default="bybit")
    parser.add_argument("--market", choices=("spot", "perpetual"), default="perpetual")
    parser.add_argument("--symbols", nargs="+", default=["BTC", "ETH", "TON"])
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--backfill-hours", type=int, default=24)
    parser.add_argument("--backfill-only", action="store_true")
    return parser.parse_args()


async def _run_stream(
    database: Database,
    adapter: BybitAdapter | OkxAdapter,
    instrument: Instrument,
    stream: str,
    interval: str,
) -> None:
    async with database.session_factory() as session:
        collector = MarketCollector(adapter, MarketDataRepository(session, auto_commit=True))
        if stream == "candles":
            await collector.consume_candles(instrument, interval)
        elif stream == "trades":
            await collector.consume_trades(instrument)
        else:
            await collector.consume_orderbook(instrument)


async def _supervise_stream(
    database: Database,
    adapter: BybitAdapter | OkxAdapter,
    instrument: Instrument,
    stream: str,
    interval: str,
) -> None:
    logger = structlog.get_logger()
    while True:
        try:
            await _run_stream(database, adapter, instrument, stream, interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "market_stream_restarted",
                instrument=instrument.symbol,
                stream=stream,
            )
            await asyncio.sleep(2)


async def _poll_gap_backfill(
    database: Database,
    adapter: BybitAdapter | OkxAdapter,
    instrument: Instrument,
    interval: str,
) -> None:
    logger = structlog.get_logger()
    while True:
        await asyncio.sleep(300)
        try:
            async with database.session_factory() as session:
                collector = MarketCollector(
                    adapter, MarketDataRepository(session, auto_commit=True)
                )
                end = datetime.now(UTC)
                await collector.backfill_candles(
                    instrument,
                    interval,
                    end - timedelta(minutes=15),
                    end,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("gap_backfill_failed", instrument=instrument.symbol)


async def _poll_derivatives(
    database: Database,
    adapter: BybitAdapter | OkxAdapter,
    instrument: Instrument,
) -> None:
    logger = structlog.get_logger()
    while True:
        try:
            async with database.session_factory() as session:
                collector = MarketCollector(
                    adapter, MarketDataRepository(session, auto_commit=True)
                )
                end = datetime.now(UTC)
                await collector.collect_derivatives_metrics(
                    instrument, end - timedelta(hours=10), end
                )
        except Exception:
            logger.exception("derivatives_poll_failed", instrument=instrument.symbol)
        await asyncio.sleep(300)


async def _poll_fx(database: Database, rest: AioHttpRestTransport) -> None:
    logger = structlog.get_logger()
    adapter = CoinGeckoFxAdapter(rest)
    while True:
        try:
            rate = await adapter.get_usdt_rub()
            async with database.session_factory() as session:
                await MarketDataRepository(session, auto_commit=True).upsert_fx_rate(rate)
        except Exception:
            logger.exception("fx_poll_failed")
        await asyncio.sleep(300)


async def run(args: argparse.Namespace) -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    kind = InstrumentKind.SPOT if args.market == "spot" else InstrumentKind.LINEAR_PERPETUAL
    instruments = [Instrument(symbol, kind=kind) for symbol in args.symbols]
    heartbeat_task = asyncio.create_task(
        run_heartbeat_loop(database, Component.MARKET, f"collector-{args.provider}"),
        name=f"market-heartbeat-{args.provider}",
    )
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            rest = AioHttpRestTransport(session)
            websocket = AioHttpWebSocketTransport(session)
            adapter: BybitAdapter | OkxAdapter
            adapter = (
                BybitAdapter(rest, websocket)
                if args.provider == "bybit"
                else OkxAdapter(rest, websocket)
            )
            async with database.session_factory() as db_session:
                collector = MarketCollector(
                    adapter, MarketDataRepository(db_session, auto_commit=True)
                )
                end = datetime.now(UTC)
                start = end - timedelta(hours=args.backfill_hours)
                for instrument in instruments:
                    await collector.backfill_candles(instrument, args.interval, start, end)
            if args.backfill_only:
                return
            async with asyncio.TaskGroup() as group:
                for instrument in instruments:
                    for stream in ("candles", "trades", "orderbook"):
                        group.create_task(
                            _supervise_stream(
                                database,
                                adapter,
                                instrument,
                                stream,
                                args.interval,
                            )
                        )
                    group.create_task(_poll_derivatives(database, adapter, instrument))
                    group.create_task(
                        _poll_gap_backfill(database, adapter, instrument, args.interval)
                    )
                if args.provider == "bybit":
                    group.create_task(_poll_fx(database, rest))
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        await database.dispose()


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
