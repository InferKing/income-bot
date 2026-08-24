from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.jobs.retention import delete_expired_orderbook_batch
from income_tg.storage.trading_models import (
    FeatureVectorRecord,
    InstrumentRecord,
    OrderbookSnapshotRecord,
)

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


async def test_retention_deletes_only_expired_snapshots_and_keeps_features(
    session: AsyncSession,
) -> None:
    instrument = InstrumentRecord(
        canonical_symbol="BTC/USDT:PERP",
        base_asset="BTC",
        quote_asset="USDT",
        market_type="linear_perpetual",
    )
    session.add(instrument)
    await session.flush()
    session.add_all(
        [
            _snapshot(1, instrument.id, NOW - timedelta(days=8)),
            _snapshot(2, instrument.id, NOW - timedelta(days=7)),
            _snapshot(3, instrument.id, NOW - timedelta(days=1)),
            FeatureVectorRecord(
                instrument_id=instrument.id,
                horizon="15m",
                as_of=NOW - timedelta(days=30),
                data_cutoff=NOW - timedelta(days=30),
                schema_hash="schema-v1",
                names=["spread_bps"],
                values=[1.0],
            ),
        ]
    )
    await session.flush()

    deleted = await delete_expired_orderbook_batch(
        session,
        cutoff=NOW - timedelta(days=7),
        batch_size=10,
    )

    remaining_ids = tuple(
        await session.scalars(
            select(OrderbookSnapshotRecord.id).order_by(OrderbookSnapshotRecord.id)
        )
    )
    feature_count = await session.scalar(select(func.count()).select_from(FeatureVectorRecord))
    assert deleted == 1
    assert remaining_ids == (2, 3)
    assert feature_count == 1


async def test_retention_batch_size_limits_each_transaction(session: AsyncSession) -> None:
    instrument = InstrumentRecord(
        canonical_symbol="ETH/USDT:PERP",
        base_asset="ETH",
        quote_asset="USDT",
        market_type="linear_perpetual",
    )
    session.add(instrument)
    await session.flush()
    session.add_all(
        [_snapshot(index, instrument.id, NOW - timedelta(days=8)) for index in range(10, 13)]
    )
    await session.flush()

    deleted = await delete_expired_orderbook_batch(
        session,
        cutoff=NOW - timedelta(days=7),
        batch_size=2,
    )

    remaining = await session.scalar(select(func.count()).select_from(OrderbookSnapshotRecord))
    assert deleted == 2
    assert remaining == 1


def _snapshot(
    identifier: int, instrument_id: UUID, captured_at: datetime
) -> OrderbookSnapshotRecord:
    return OrderbookSnapshotRecord(
        id=identifier,
        provider="bybit",
        instrument_id=instrument_id,
        captured_at=captured_at,
        sequence=identifier,
        bids=[["100", "1"]],
        asks=[["101", "1"]],
        best_bid=Decimal("100"),
        best_ask=Decimal("101"),
        spread_bps=99.5,
    )
