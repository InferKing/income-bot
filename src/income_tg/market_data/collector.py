"""Quality-gated persistence orchestration for REST and WebSocket data."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from income_tg.market_data.adapters.base import MarketDataAdapter
from income_tg.market_data.backfill import BackfillOrchestrator, BackfillReport
from income_tg.market_data.orderbook import (
    LocalOrderBook,
    OrderBookError,
    OrderBookNotReadyError,
    OrderBookSequenceGapError,
    OrderBookView,
)
from income_tg.market_data.quality import (
    MarketDataQualityError,
    assert_fresh,
    assert_prices_close,
)
from income_tg.market_data.schemas import (
    Candle,
    DerivativesMetrics,
    FxRate,
    Instrument,
    OrderBookUpdate,
    Trade,
)


class CollectorRepository(Protocol):
    async def upsert_candles(self, candles: list[Candle]) -> int: ...

    async def insert_trade(self, trade: Trade) -> bool: ...

    async def insert_orderbook_snapshot(
        self, update: OrderBookUpdate, view: OrderBookView
    ) -> None: ...

    async def upsert_derivatives_metrics(self, metrics: list[DerivativesMetrics]) -> int: ...

    async def upsert_fx_rate(self, rate: FxRate) -> bool: ...

    async def record_quality_event(
        self,
        *,
        provider: str,
        event_type: str,
        severity: str,
        details: dict[str, object],
        instrument: Instrument | None = None,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class CollectorPolicy:
    candle_maximum_lag: timedelta = timedelta(minutes=2)
    trade_maximum_age: timedelta = timedelta(seconds=30)
    orderbook_maximum_age: timedelta = timedelta(seconds=30)
    derivatives_maximum_age: timedelta = timedelta(hours=9)
    maximum_price_divergence: Decimal = Decimal("0.01")


class MarketCollector:
    def __init__(
        self,
        adapter: MarketDataAdapter,
        repository: CollectorRepository,
        *,
        policy: CollectorPolicy | None = None,
        orderbook_depth: int = 50,
    ) -> None:
        self._adapter = adapter
        self._repository = repository
        self._policy = policy or CollectorPolicy()
        self._orderbook_depth = orderbook_depth
        self._books: dict[tuple[Instrument, str], LocalOrderBook] = {}

    async def backfill_candles(
        self,
        instrument: Instrument,
        interval: str,
        start: datetime,
        end: datetime,
        *,
        chunk_bars: int = 250,
    ) -> BackfillReport:
        orchestrator = BackfillOrchestrator(self._adapter, self._repository, chunk_bars=chunk_bars)
        report = await orchestrator.run(instrument, interval, start, end)
        if report.gaps:
            await self._repository.record_quality_event(
                provider="collector",
                event_type="CANDLE_GAP",
                severity="WARNING",
                instrument=instrument,
                details={
                    "interval": interval,
                    "gap_count": len(report.gaps),
                    "first_gap": report.gaps[0].expected_at.isoformat(),
                },
            )
        return report

    async def handle_candle(self, candle: Candle, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        observed_at = (
            candle.opened_at + timedelta(seconds=candle.interval_seconds)
            if candle.closed
            else candle.opened_at
        )
        try:
            assert_fresh(
                observed_at,
                maximum_age=self._policy.candle_maximum_lag,
                now=current,
            )
        except MarketDataQualityError as exc:
            await self._quality_failure(candle.source.value, candle.instrument, "STALE_CANDLE", exc)
            return False
        await self._repository.upsert_candles([candle])
        return True

    async def handle_trade(self, trade: Trade, *, now: datetime | None = None) -> bool:
        try:
            assert_fresh(
                trade.occurred_at,
                maximum_age=self._policy.trade_maximum_age,
                now=now,
            )
        except MarketDataQualityError as exc:
            await self._quality_failure(trade.source.value, trade.instrument, "STALE_TRADE", exc)
            return False
        return await self._repository.insert_trade(trade)

    async def handle_orderbook(
        self, update: OrderBookUpdate, *, now: datetime | None = None
    ) -> bool:
        try:
            assert_fresh(
                update.occurred_at,
                maximum_age=self._policy.orderbook_maximum_age,
                now=now,
            )
        except MarketDataQualityError as exc:
            await self._quality_failure(
                update.source.value, update.instrument, "STALE_ORDERBOOK", exc
            )
            return False
        key = (update.instrument, update.source.value)
        book = self._books.setdefault(
            key, LocalOrderBook(update.instrument, depth=self._orderbook_depth)
        )
        try:
            book.apply(update)
            view = book.view()
        except OrderBookSequenceGapError as exc:
            await self._quality_failure(
                update.source.value, update.instrument, "ORDERBOOK_SEQUENCE_GAP", exc
            )
            return False
        except (OrderBookNotReadyError, OrderBookError) as exc:
            await self._quality_failure(
                update.source.value, update.instrument, "INVALID_ORDERBOOK", exc
            )
            return False
        await self._repository.insert_orderbook_snapshot(update, view)
        return True

    async def collect_derivatives_metrics(
        self, instrument: Instrument, start: datetime, end: datetime
    ) -> int:
        metrics = await self._adapter.get_derivatives_metrics(instrument, start, end)
        accepted: list[DerivativesMetrics] = []
        for metric in metrics:
            try:
                assert_fresh(
                    metric.occurred_at,
                    maximum_age=self._policy.derivatives_maximum_age,
                    now=end,
                )
            except MarketDataQualityError as exc:
                await self._quality_failure(
                    metric.source.value, metric.instrument, "STALE_DERIVATIVES", exc
                )
            else:
                accepted.append(metric)
        return await self._repository.upsert_derivatives_metrics(accepted)

    async def persist_fx_rate(self, rate: FxRate) -> bool:
        return await self._repository.upsert_fx_rate(rate)

    async def validate_reference_price(
        self,
        *,
        primary: Decimal,
        reserve: Decimal,
        instrument: Instrument,
        primary_provider: str,
    ) -> bool:
        try:
            assert_prices_close(
                primary,
                reserve,
                maximum_relative_difference=self._policy.maximum_price_divergence,
            )
        except MarketDataQualityError as exc:
            await self._quality_failure(primary_provider, instrument, "PRICE_DIVERGENCE", exc)
            return False
        return True

    async def consume_candles(
        self, instrument: Instrument, interval: str, *, limit: int | None = None
    ) -> int:
        return await self._consume(
            self._adapter.stream_candles(instrument, interval), self.handle_candle, limit
        )

    async def consume_trades(self, instrument: Instrument, *, limit: int | None = None) -> int:
        return await self._consume(
            self._adapter.stream_trades(instrument), self.handle_trade, limit
        )

    async def consume_orderbook(
        self, instrument: Instrument, *, depth: int = 50, limit: int | None = None
    ) -> int:
        return await self._consume(
            self._adapter.stream_orderbook(instrument, depth), self.handle_orderbook, limit
        )

    async def _consume(
        self, stream: AsyncIterator[object], handler: object, limit: int | None
    ) -> int:
        accepted = 0
        processed = 0
        async for event in stream:
            result = await handler(event)  # type: ignore[operator]
            accepted += bool(result)
            processed += 1
            if limit is not None and processed >= limit:
                break
        return accepted

    async def _quality_failure(
        self, provider: str, instrument: Instrument, event_type: str, error: Exception
    ) -> None:
        await self._repository.record_quality_event(
            provider=provider,
            event_type=event_type,
            severity="ERROR",
            instrument=instrument,
            details={"error": str(error)},
        )
