from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.storage.trading_models import InstrumentRecord


def canonical_instrument_symbol(symbol: str) -> str:
    """Return the symbol representation persisted in the instruments table."""

    normalized = symbol.strip().upper()
    pair, separator, suffix = normalized.partition(":")
    if separator and suffix != "PERP":
        raise ValueError(f"unsupported instrument suffix: {suffix}")
    if not pair or pair.count("/") != 1:
        raise ValueError("instrument symbol must use BASE/QUOTE format")
    base, quote = pair.split("/", maxsplit=1)
    if not base or not quote or not base.isalnum() or not quote.isalnum():
        raise ValueError("instrument assets must be non-empty alphanumeric values")
    if base == quote:
        raise ValueError("instrument base and quote assets must differ")
    return f"{base}/{quote}"


async def find_instrument(
    session: AsyncSession,
    symbol: str,
    *,
    market_type: str,
) -> InstrumentRecord | None:
    """Resolve a display symbol against its canonical database identity."""

    record = await session.scalar(
        select(InstrumentRecord).where(
            InstrumentRecord.canonical_symbol == canonical_instrument_symbol(symbol),
            InstrumentRecord.market_type == market_type,
        )
    )
    return record
