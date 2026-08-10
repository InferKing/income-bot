from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.features.pipeline import FeatureVector
from income_tg.storage.trading_models import FeatureVectorRecord


def feature_schema_hash(names: tuple[str, ...]) -> str:
    return hashlib.sha256("\x1f".join(names).encode()).hexdigest()


class FeatureRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save(
        self,
        *,
        instrument_id: UUID,
        horizon: str,
        vector: FeatureVector,
    ) -> FeatureVectorRecord:
        schema_hash = feature_schema_hash(vector.names)
        existing = await self.session.scalar(
            select(FeatureVectorRecord).where(
                FeatureVectorRecord.instrument_id == instrument_id,
                FeatureVectorRecord.horizon == horizon,
                FeatureVectorRecord.as_of == vector.as_of,
                FeatureVectorRecord.schema_hash == schema_hash,
            )
        )
        if existing is not None:
            return existing
        record = FeatureVectorRecord(
            instrument_id=instrument_id,
            horizon=horizon,
            as_of=vector.as_of,
            data_cutoff=vector.data_cutoff,
            schema_hash=schema_hash,
            names=list(vector.names),
            values=list(vector.values),
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def list(
        self,
        *,
        instrument_id: UUID,
        horizon: str,
        limit: int = 100_000,
    ) -> list[FeatureVectorRecord]:
        return list(
            await self.session.scalars(
                select(FeatureVectorRecord)
                .where(
                    FeatureVectorRecord.instrument_id == instrument_id,
                    FeatureVectorRecord.horizon == horizon,
                )
                .order_by(FeatureVectorRecord.as_of)
                .limit(limit)
            )
        )
