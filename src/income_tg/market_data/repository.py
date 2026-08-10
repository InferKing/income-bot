"""SQLAlchemy persistence for canonical market data."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.market_data.orderbook import OrderBookView
from income_tg.market_data.schemas import (
    Candle,
    DerivativesMetrics,
    FxRate,
    Instrument,
    OrderBookUpdate,
    Trade,
)
from income_tg.storage.trading_models import (
    DataQualityEventRecord,
    DerivativeMetricRecord,
    FxRateRecord,
    InstrumentRecord,
    MarketCandleRecord,
    MarketTradeRecord,
    OrderbookSnapshotRecord,
)


class UnsupportedDatabaseError(RuntimeError):
    pass


class MarketDataRepository:
    """Unit-of-work scoped repository; the caller owns commit/rollback."""

    def __init__(self, session: AsyncSession, *, auto_commit: bool = False) -> None:
        self._session = session
        self._auto_commit = auto_commit
        self._instrument_ids: dict[Instrument, UUID] = {}

    async def _finish(self) -> None:
        if self._auto_commit:
            await self._session.commit()
        else:
            await self._session.flush()

    @property
    def _dialect(self) -> str:
        bind = self._session.get_bind()
        return bind.dialect.name

    def _insert(self, table: Any) -> Any:
        if self._dialect == "postgresql":
            return postgresql_insert(table)
        if self._dialect == "sqlite":
            return sqlite_insert(table)
        raise UnsupportedDatabaseError(f"unsupported SQL dialect: {self._dialect}")

    async def _next_sqlite_id(self, model: type[object], id_column: object) -> int | None:
        if self._dialect != "sqlite":
            return None
        value = await self._session.scalar(select(func.max(id_column)))
        return int(str(value)) + 1 if value is not None else 1

    async def get_or_create_instrument(self, instrument: Instrument) -> UUID:
        cached = self._instrument_ids.get(instrument)
        if cached is not None:
            return cached
        canonical_symbol = f"{instrument.base}/{instrument.quote}"
        market_type = instrument.kind.value
        query = select(InstrumentRecord.id).where(
            InstrumentRecord.canonical_symbol == canonical_symbol,
            InstrumentRecord.market_type == market_type,
        )
        existing = await self._session.scalar(query)
        if existing is None:
            identifier = uuid4()
            statement = self._insert(InstrumentRecord.__table__).values(
                id=identifier,
                canonical_symbol=canonical_symbol,
                base_asset=instrument.base,
                quote_asset=instrument.quote,
                market_type=market_type,
                is_active=True,
                metadata={},
            )
            statement = statement.on_conflict_do_nothing(
                index_elements=["canonical_symbol", "market_type"]
            )
            await self._session.execute(statement)
            existing = await self._session.scalar(query)
            if existing is None:
                raise RuntimeError("failed to create canonical instrument")
        self._instrument_ids[instrument] = existing
        return existing

    async def upsert_candles(self, candles: list[Candle]) -> int:
        inserted = 0
        for candle in candles:
            instrument_id = await self.get_or_create_instrument(candle.instrument)
            key = (
                candle.source.value,
                instrument_id,
                candle.interval_seconds,
                candle.opened_at,
            )
            exists = await self._session.scalar(
                select(MarketCandleRecord.id).where(
                    MarketCandleRecord.provider == key[0],
                    MarketCandleRecord.instrument_id == key[1],
                    MarketCandleRecord.interval_seconds == key[2],
                    MarketCandleRecord.opened_at == key[3],
                )
            )
            values: dict[str, object] = {
                "provider": candle.source.value,
                "instrument_id": instrument_id,
                "interval_seconds": candle.interval_seconds,
                "opened_at": candle.opened_at,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume_base,
                "turnover": candle.turnover_quote,
                "is_closed": candle.closed,
            }
            sqlite_id = await self._next_sqlite_id(MarketCandleRecord, MarketCandleRecord.id)
            if sqlite_id is not None:
                values["id"] = exists if exists is not None else sqlite_id
            statement = self._insert(MarketCandleRecord.__table__).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=["provider", "instrument_id", "interval_seconds", "opened_at"],
                set_={
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume_base,
                    "turnover": candle.turnover_quote,
                    "is_closed": candle.closed,
                    "received_at": datetime.now(UTC),
                },
            )
            await self._session.execute(statement)
            inserted += exists is None
        await self._finish()
        return inserted

    async def insert_trade(self, trade: Trade) -> bool:
        instrument_id = await self.get_or_create_instrument(trade.instrument)
        values: dict[str, object] = {
            "provider": trade.source.value,
            "instrument_id": instrument_id,
            "provider_trade_id": trade.trade_id,
            "occurred_at": trade.occurred_at,
            "side": trade.taker_side.value,
            "price": trade.price,
            "quantity": trade.quantity_base,
        }
        sqlite_id = await self._next_sqlite_id(MarketTradeRecord, MarketTradeRecord.id)
        if sqlite_id is not None:
            values["id"] = sqlite_id
        statement = self._insert(MarketTradeRecord.__table__).values(**values)
        statement = statement.on_conflict_do_nothing(
            index_elements=["provider", "instrument_id", "provider_trade_id"]
        )
        result = await self._session.execute(statement)
        await self._finish()
        return bool(getattr(result, "rowcount", 0))

    async def insert_orderbook_snapshot(self, update: OrderBookUpdate, view: OrderBookView) -> None:
        instrument_id = await self.get_or_create_instrument(update.instrument)
        if view.best_bid is None or view.best_ask is None:
            raise ValueError("cannot persist an order book without both sides")
        mid = (view.best_bid + view.best_ask) / 2
        spread_bps = float((view.best_ask - view.best_bid) / mid * Decimal("10000"))
        values: dict[str, object] = {
            "provider": update.source.value,
            "instrument_id": instrument_id,
            "captured_at": update.occurred_at,
            "sequence": view.sequence,
            "bids": [[str(level.price), str(level.quantity_base)] for level in view.bids],
            "asks": [[str(level.price), str(level.quantity_base)] for level in view.asks],
            "best_bid": view.best_bid,
            "best_ask": view.best_ask,
            "spread_bps": spread_bps,
        }
        sqlite_id = await self._next_sqlite_id(OrderbookSnapshotRecord, OrderbookSnapshotRecord.id)
        if sqlite_id is not None:
            values["id"] = sqlite_id
        await self._session.execute(
            self._insert(OrderbookSnapshotRecord.__table__).values(**values)
        )
        await self._finish()

    async def upsert_derivatives_metrics(self, metrics: list[DerivativesMetrics]) -> int:
        inserted = 0
        for metric in metrics:
            instrument_id = await self.get_or_create_instrument(metric.instrument)
            exists = await self._session.scalar(
                select(DerivativeMetricRecord.id).where(
                    DerivativeMetricRecord.provider == metric.source.value,
                    DerivativeMetricRecord.instrument_id == instrument_id,
                    DerivativeMetricRecord.observed_at == metric.occurred_at,
                )
            )
            values: dict[str, object] = {
                "provider": metric.source.value,
                "instrument_id": instrument_id,
                "observed_at": metric.occurred_at,
                "funding_rate": metric.funding_rate,
                "open_interest": metric.open_interest_base,
                "mark_price": metric.mark_price,
                "index_price": metric.index_price,
            }
            sqlite_id = await self._next_sqlite_id(
                DerivativeMetricRecord, DerivativeMetricRecord.id
            )
            if sqlite_id is not None:
                values["id"] = exists if exists is not None else sqlite_id
            statement = self._insert(DerivativeMetricRecord.__table__).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=["provider", "instrument_id", "observed_at"],
                set_={
                    "funding_rate": metric.funding_rate,
                    "open_interest": metric.open_interest_base,
                    "mark_price": metric.mark_price,
                    "index_price": metric.index_price,
                },
            )
            await self._session.execute(statement)
            inserted += exists is None
        await self._finish()
        return inserted

    async def upsert_fx_rate(self, rate: FxRate) -> bool:
        values: dict[str, object] = {
            "base": rate.base,
            "quote": rate.quote,
            "provider": rate.source,
            "observed_at": rate.observed_at,
            "rate": rate.rate,
            "is_derived": False,
        }
        sqlite_id = await self._next_sqlite_id(FxRateRecord, FxRateRecord.id)
        if sqlite_id is not None:
            values["id"] = sqlite_id
        statement = self._insert(FxRateRecord.__table__).values(**values)
        statement = statement.on_conflict_do_nothing(
            index_elements=["base", "quote", "provider", "observed_at"]
        )
        result = await self._session.execute(statement)
        await self._finish()
        return bool(getattr(result, "rowcount", 0))

    async def record_quality_event(
        self,
        *,
        provider: str,
        event_type: str,
        severity: str,
        details: dict[str, object],
        instrument: Instrument | None = None,
    ) -> UUID:
        instrument_id = (
            await self.get_or_create_instrument(instrument) if instrument is not None else None
        )
        record = DataQualityEventRecord(
            provider=provider,
            instrument_id=instrument_id,
            event_type=event_type,
            severity=severity,
            started_at=datetime.now(UTC),
            details=details,
        )
        self._session.add(record)
        await self._finish()
        return record.id
