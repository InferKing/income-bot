from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from income_tg.market_data.backfill import BackfillOrchestrator
from income_tg.market_data.schemas import Candle, DataSource, Instrument


class FakeAdapter:
    def __init__(self, candles: list[Candle]) -> None:
        self.candles = candles
        self.calls = 0

    async def get_candles(
        self, instrument: Instrument, interval: str, start: datetime, end: datetime
    ) -> list[Candle]:
        del instrument, interval
        self.calls += 1
        return [item for item in self.candles if start <= item.opened_at < end]

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(name)


class MemoryStore:
    def __init__(self) -> None:
        self.identities: set[tuple[str, int, datetime]] = set()

    async def upsert_candles(self, candles: list[Candle]) -> int:
        before = len(self.identities)
        self.identities.update(item.identity for item in candles)
        return len(self.identities) - before


def make_candle(at: datetime) -> Candle:
    return Candle(
        instrument=Instrument("BTC"),
        interval_seconds=60,
        opened_at=at,
        open=Decimal("10"),
        high=Decimal("11"),
        low=Decimal("9"),
        close=Decimal("10"),
        volume_base=Decimal("1"),
        turnover_quote=Decimal("10"),
        closed=True,
        source=DataSource.BYBIT,
    )


@pytest.mark.asyncio
async def test_repeated_backfill_is_idempotent_and_chunked() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    candles = [make_candle(start + timedelta(minutes=index)) for index in range(5)]
    adapter = FakeAdapter(candles)
    store = MemoryStore()
    orchestrator = BackfillOrchestrator(adapter, store, chunk_bars=2)  # type: ignore[arg-type]

    first = await orchestrator.run(Instrument("BTC"), "1m", start, start + timedelta(minutes=5))
    second = await orchestrator.run(Instrument("BTC"), "1m", start, start + timedelta(minutes=5))

    assert first.inserted == 5
    assert second.inserted == 0
    assert not first.gaps
    assert adapter.calls == 6
