from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.features.pipeline import FeatureVector
from income_tg.features.repository import FeatureRepository
from income_tg.storage.trading_models import InstrumentRecord


async def test_feature_repository_is_idempotent(session: AsyncSession) -> None:
    instrument = InstrumentRecord(
        canonical_symbol="BTC/USDT:PERP",
        base_asset="BTC",
        quote_asset="USDT",
        market_type="linear_perpetual",
        is_active=True,
        metadata_json={"test": str(uuid4())},
    )
    session.add(instrument)
    await session.flush()
    vector = FeatureVector(
        instrument="BTCUSDT",
        as_of=datetime(2026, 1, 1, tzinfo=UTC),
        data_cutoff=datetime(2026, 1, 1, tzinfo=UTC),
        names=("a", "b"),
        values=(1.0, 2.0),
    )
    repository = FeatureRepository(session)
    first = await repository.save(instrument_id=instrument.id, horizon="15m", vector=vector)
    second = await repository.save(instrument_id=instrument.id, horizon="15m", vector=vector)
    assert first.id == second.id
    assert len(await repository.list(instrument_id=instrument.id, horizon="15m")) == 1
