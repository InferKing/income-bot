from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.features.service import OnlineFeatureService
from income_tg.storage.trading_models import (
    InstrumentRecord,
    MarketCandleRecord,
    OrderbookSnapshotRecord,
)


async def test_feature_service_uses_latest_quality_complete_candle(
    session: AsyncSession,
) -> None:
    instrument = InstrumentRecord(
        canonical_symbol="BTC/USDT",
        base_asset="BTC",
        quote_asset="USDT",
        market_type="linear_perpetual",
        is_active=True,
        metadata_json={},
    )
    session.add(instrument)
    await session.flush()
    start = datetime(2026, 8, 17, 12, tzinfo=UTC)
    candle_id = 1
    for index in range(30):
        opened_at = start + timedelta(minutes=index)
        price = Decimal(100 + index)
        session.add(
            MarketCandleRecord(
                id=candle_id,
                provider="bybit",
                instrument_id=instrument.id,
                interval_seconds=60,
                opened_at=opened_at,
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=Decimal("10"),
                turnover=Decimal("1000"),
                is_closed=True,
            )
        )
        candle_id += 1
        if index < 29:
            session.add(
                MarketCandleRecord(
                    id=candle_id,
                    provider="okx",
                    instrument_id=instrument.id,
                    interval_seconds=60,
                    opened_at=opened_at,
                    open=price,
                    high=price + 1,
                    low=price - 1,
                    close=price,
                    volume=Decimal("10"),
                    turnover=Decimal("1000"),
                    is_closed=True,
                )
            )
            candle_id += 1
    selected_as_of = start + timedelta(minutes=29)
    session.add(
        OrderbookSnapshotRecord(
            id=1,
            provider="bybit",
            instrument_id=instrument.id,
            captured_at=selected_as_of - timedelta(seconds=1),
            sequence=1,
            bids=[["128", "2"]],
            asks=[["130", "2"]],
            best_bid=Decimal("128"),
            best_ask=Decimal("130"),
            spread_bps=1.0,
        )
    )
    await session.flush()

    service = OnlineFeatureService(session)
    first = await service.build_latest(instrument)
    second = await service.build_latest(instrument)

    assert first.vectors_created == 4
    assert first.outcome == "created"
    assert first.as_of == selected_as_of
    assert first.candidate_lag_seconds == 60
    assert second.vectors_created == 0
    assert second.outcome == "up_to_date"


async def test_feature_service_reports_missing_reserve_candles(session: AsyncSession) -> None:
    instrument = InstrumentRecord(
        canonical_symbol="TON/USDT",
        base_asset="TON",
        quote_asset="USDT",
        market_type="linear_perpetual",
        is_active=True,
        metadata_json={},
    )
    session.add(instrument)
    await session.flush()
    start = datetime(2026, 8, 17, 12, tzinfo=UTC)
    for index in range(20):
        opened_at = start + timedelta(minutes=index)
        price = Decimal(10 + index)
        session.add(
            MarketCandleRecord(
                id=index + 1,
                provider="bybit",
                instrument_id=instrument.id,
                interval_seconds=60,
                opened_at=opened_at,
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=Decimal("10"),
                turnover=Decimal("100"),
                is_closed=True,
            )
        )
    await session.flush()

    result = await OnlineFeatureService(session).build_latest(instrument)

    assert result.vectors_created == 0
    assert result.outcome == "reserve_candle_missing"
    assert result.as_of == start + timedelta(minutes=20)
