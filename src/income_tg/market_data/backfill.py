"""Chunked, idempotent candle backfill orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from income_tg.market_data.adapters.base import MarketDataAdapter
from income_tg.market_data.normalization import interval_seconds
from income_tg.market_data.quality import CandleGap, find_candle_gaps
from income_tg.market_data.schemas import Candle, Instrument


class CandleStore(Protocol):
    async def upsert_candles(self, candles: list[Candle]) -> int:
        """Persist candles idempotently and return the number newly inserted."""
        ...


@dataclass(frozen=True, slots=True)
class BackfillReport:
    downloaded: int
    unique: int
    inserted: int
    gaps: tuple[CandleGap, ...]


class BackfillOrchestrator:
    def __init__(
        self,
        adapter: MarketDataAdapter,
        store: CandleStore,
        *,
        chunk_bars: int = 250,
    ) -> None:
        if chunk_bars <= 0:
            raise ValueError("chunk_bars must be positive")
        self._adapter = adapter
        self._store = store
        self._chunk_bars = chunk_bars

    async def run(
        self,
        instrument: Instrument,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> BackfillReport:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("backfill boundaries must be timezone-aware")
        if start >= end:
            raise ValueError("backfill start must be earlier than end")
        seconds = interval_seconds(interval)
        start = datetime.fromtimestamp(int(start.timestamp()) // seconds * seconds, tz=UTC)
        end = datetime.fromtimestamp(int(end.timestamp()) // seconds * seconds, tz=UTC)
        if start >= end:
            raise ValueError("aligned backfill range contains no complete bars")
        chunk = timedelta(seconds=seconds * self._chunk_bars)
        cursor = start
        downloaded = 0
        inserted = 0
        unique_by_identity: dict[tuple[str, int, datetime], Candle] = {}
        while cursor < end:
            chunk_end = min(cursor + chunk, end)
            candles = await self._adapter.get_candles(instrument, interval, cursor, chunk_end)
            downloaded += len(candles)
            normalized = [
                candle
                for candle in candles
                if cursor <= candle.opened_at < chunk_end and candle.closed
            ]
            for candle in normalized:
                unique_by_identity[candle.identity] = candle
            inserted += await self._store.upsert_candles(normalized)
            cursor = chunk_end
        unique = sorted(unique_by_identity.values(), key=lambda item: item.opened_at)
        gaps = find_candle_gaps(unique, start, end, seconds)
        return BackfillReport(
            downloaded=downloaded,
            unique=len(unique),
            inserted=inserted,
            gaps=tuple(gaps),
        )
