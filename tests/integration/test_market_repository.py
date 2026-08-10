from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.market_data.repository import MarketDataRepository
from income_tg.market_data.schemas import (
    Candle,
    DataSource,
    Instrument,
    InstrumentKind,
    Side,
    Trade,
)
from income_tg.storage.trading_models import (
    InstrumentRecord,
    MarketCandleRecord,
    MarketTradeRecord,
)


def candle(*, close: str = "11", closed: bool = True) -> Candle:
    return Candle(
        instrument=Instrument("btc"),
        interval_seconds=60,
        opened_at=datetime(2026, 8, 10, 10, tzinfo=UTC),
        open=Decimal("10"),
        high=Decimal("12"),
        low=Decimal("9"),
        close=Decimal(close),
        volume_base=Decimal("2"),
        turnover_quote=Decimal("22"),
        closed=closed,
        source=DataSource.BYBIT,
    )


@pytest.mark.asyncio
async def test_get_or_create_instrument_is_canonical(session: AsyncSession) -> None:
    repository = MarketDataRepository(session)
    first = await repository.get_or_create_instrument(Instrument("btc"))
    second = await repository.get_or_create_instrument(Instrument("BTC"))
    perpetual = await repository.get_or_create_instrument(
        Instrument("BTC", kind=InstrumentKind.LINEAR_PERPETUAL)
    )

    assert first == second
    assert perpetual != first
    records = (await session.scalars(select(InstrumentRecord))).all()
    assert [(item.canonical_symbol, item.market_type) for item in records] == [
        ("BTC/USDT", "spot"),
        ("BTC/USDT", "linear_perpetual"),
    ]


@pytest.mark.asyncio
async def test_candle_upsert_is_idempotent_and_refreshes_open_bar(
    session: AsyncSession,
) -> None:
    repository = MarketDataRepository(session)

    assert await repository.upsert_candles([candle(close="11", closed=False)]) == 1
    assert await repository.upsert_candles([candle(close="12", closed=True)]) == 0

    assert await session.scalar(select(func.count()).select_from(MarketCandleRecord)) == 1
    record = await session.scalar(select(MarketCandleRecord))
    assert record is not None
    assert record.close == Decimal("12")
    assert record.is_closed


@pytest.mark.asyncio
async def test_trade_insert_deduplicates_provider_trade_id(session: AsyncSession) -> None:
    repository = MarketDataRepository(session)
    trade = Trade(
        instrument=Instrument("ETH"),
        trade_id="provider-42",
        occurred_at=datetime.now(UTC),
        price=Decimal("4000"),
        quantity_base=Decimal("0.5"),
        taker_side=Side.BUY,
        source=DataSource.BYBIT,
    )

    assert await repository.insert_trade(trade)
    assert not await repository.insert_trade(trade)
    assert await session.scalar(select(func.count()).select_from(MarketTradeRecord)) == 1
