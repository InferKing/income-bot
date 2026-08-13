from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.storage.instruments import canonical_instrument_symbol, find_instrument
from income_tg.storage.trading_models import InstrumentRecord


@pytest.mark.parametrize(
    ("display_symbol", "expected"),
    [
        ("BTC/USDT", "BTC/USDT"),
        ("BTC/USDT:PERP", "BTC/USDT"),
        (" btc/usdt:perp ", "BTC/USDT"),
    ],
)
def test_canonical_instrument_symbol(display_symbol: str, expected: str) -> None:
    assert canonical_instrument_symbol(display_symbol) == expected


@pytest.mark.parametrize("symbol", ["BTCUSDT", "BTC/", "/USDT", "BTC/BTC", "BTC/USDT:SPOT"])
def test_canonical_instrument_symbol_rejects_invalid_values(symbol: str) -> None:
    with pytest.raises(ValueError):
        canonical_instrument_symbol(symbol)


async def test_perpetual_display_symbol_resolves_canonical_record(session: AsyncSession) -> None:
    stored = InstrumentRecord(
        canonical_symbol="BTC/USDT",
        base_asset="BTC",
        quote_asset="USDT",
        market_type="linear_perpetual",
        is_active=True,
        metadata_json={},
    )
    session.add(stored)
    await session.flush()

    resolved = await find_instrument(
        session,
        "BTC/USDT:PERP",
        market_type="linear_perpetual",
    )

    assert resolved is stored
