from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.models.dataset import load_labeled_dataset
from income_tg.storage.trading_models import (
    FeatureVectorRecord,
    InstrumentRecord,
    MarketCandleRecord,
)


async def test_labeled_dataset_uses_only_configured_candle_provider(
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
    start = datetime(2026, 8, 17, 12)
    candle_id = 1
    for index in range(60):
        opened_at = start + timedelta(minutes=index)
        for provider, close in (
            ("bybit", Decimal(100 + index)),
            ("okx", Decimal(1000 + index)),
        ):
            session.add(
                MarketCandleRecord(
                    id=candle_id,
                    provider=provider,
                    instrument_id=instrument.id,
                    interval_seconds=60,
                    opened_at=opened_at,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=Decimal("1"),
                    turnover=close,
                    is_closed=True,
                )
            )
            candle_id += 1
    for index in range(40):
        as_of = start + timedelta(minutes=index + 1)
        session.add(
            FeatureVectorRecord(
                instrument_id=instrument.id,
                horizon="15m",
                as_of=as_of,
                data_cutoff=as_of,
                schema_hash="schema",
                names=["signal"],
                values=[float(index)],
            )
        )
    await session.flush()

    labeled = await load_labeled_dataset(
        session,
        instrument_id=instrument.id,
        horizon="15m",
        horizon_duration=timedelta(minutes=15),
        candle_provider="bybit",
    )

    assert len(labeled.forward_returns) == 40
    assert labeled.forward_returns[0] == pytest.approx(115 / 100 - 1)
    assert labeled.forward_returns[-1] == pytest.approx(154 / 139 - 1)
