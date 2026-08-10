"""Atomic persistence of paper orders, fills and portfolio-ledger movements."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.common.enums import EventSource, PortfolioEventType
from income_tg.paper_trading.models import (
    CloseResult,
    Fill,
    OpenPositionResult,
    Order,
    OrderStatus,
)
from income_tg.storage.models import LedgerEntry, PortfolioEvent
from income_tg.storage.trading_models import (
    PaperFillRecord,
    PaperOrderRecord,
    PaperPositionRecord,
)


@dataclass(frozen=True, slots=True)
class PaperPersistenceResult:
    order_id: UUID
    created: bool


class PaperTradingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def has_order(self, idempotency_key: str) -> bool:
        return (
            await self._session.scalar(
                select(PaperOrderRecord.id).where(
                    PaperOrderRecord.idempotency_key == idempotency_key
                )
            )
            is not None
        )

    async def reserve_order(
        self,
        *,
        portfolio_id: UUID,
        instrument_id: UUID,
        order: Order,
        idempotency_key: str,
    ) -> PaperPersistenceResult:
        """Atomically claim a business event before creating signal side effects."""
        identifier = uuid4()
        bind = self._session.get_bind()
        statement: Any
        table = cast(Any, PaperOrderRecord.__table__)
        if bind.dialect.name == "postgresql":
            statement = postgresql_insert(table)
        elif bind.dialect.name == "sqlite":
            statement = sqlite_insert(table)
        else:
            raise RuntimeError(f"unsupported SQL dialect: {bind.dialect.name}")
        statement = statement.values(
            id=identifier,
            portfolio_id=portfolio_id,
            signal_id=None,
            instrument_id=instrument_id,
            side=order.side.value,
            order_type=order.order_type.value,
            status="PROCESSING",
            quantity=order.quantity,
            limit_price=order.limit_price,
            idempotency_key=idempotency_key,
        ).on_conflict_do_nothing(index_elements=["idempotency_key"])
        result = await self._session.execute(statement)
        if bool(getattr(result, "rowcount", 0)):
            await self._session.flush()
            return PaperPersistenceResult(identifier, True)
        existing = await self._existing(idempotency_key)
        if existing is None:
            raise RuntimeError("order reservation conflict did not return an existing order")
        return PaperPersistenceResult(existing.id, False)

    async def record_open(
        self,
        *,
        portfolio_id: UUID,
        signal_id: UUID | None,
        instrument_id: UUID,
        order: Order,
        result: OpenPositionResult,
        reference_price: Decimal,
        idempotency_key: str,
        base_asset: str,
        quote_asset: str = "USDT",
    ) -> PaperPersistenceResult:
        existing = await self._existing(idempotency_key)
        if existing is not None and existing.status != "PROCESSING":
            return PaperPersistenceResult(existing.id, False)
        record = existing or PaperOrderRecord(
            portfolio_id=portfolio_id,
            instrument_id=instrument_id,
            idempotency_key=idempotency_key,
        )
        record.signal_id = signal_id
        record.side = order.side.value
        record.order_type = order.order_type.value
        record.status = result.execution.status.value
        record.quantity = order.quantity
        record.limit_price = order.limit_price
        if existing is None:
            self._session.add(record)
        await self._session.flush()
        fill = result.execution.fill
        if fill is not None:
            await self._record_fill(record.id, fill, reference_price)
            entries = [(quote_asset, result.cash_delta, "PAPER_MARGIN_AND_FEE")]
            if result.position is not None and result.position.instrument.value == "SPOT":
                entries.append((base_asset, fill.quantity, "PAPER_POSITION"))
            await self._record_ledger(
                portfolio_id=portfolio_id,
                idempotency_key=f"paper-open:{idempotency_key}",
                occurred_at=fill.filled_at,
                entries=entries,
                details={"paper_order_id": str(record.id), "operation": "OPEN"},
            )
            if result.position is not None:
                position = result.position
                self._session.add(
                    PaperPositionRecord(
                        position_key=position.position_id,
                        portfolio_id=portfolio_id,
                        instrument_id=instrument_id,
                        side=position.side.value,
                        quantity=position.quantity,
                        entry_price=position.entry_price,
                        leverage=position.leverage,
                        margin=position.margin,
                        stop_loss=position.stop_loss,
                        take_profit=position.take_profit,
                        opening_commission=position.opening_commission,
                        funding_pnl=position.funding_pnl,
                        last_funding_at=None,
                        liquidation_price=position.liquidation_price,
                        status="OPEN",
                        opened_at=position.opened_at,
                    )
                )
        await self._session.flush()
        return PaperPersistenceResult(record.id, True)

    async def record_close(
        self,
        *,
        portfolio_id: UUID,
        signal_id: UUID | None,
        instrument_id: UUID,
        result: CloseResult,
        reference_price: Decimal,
        idempotency_key: str,
        base_asset: str,
        quote_asset: str = "USDT",
    ) -> PaperPersistenceResult:
        existing = await self._existing(idempotency_key)
        if existing is not None and existing.status != "PROCESSING":
            return PaperPersistenceResult(existing.id, False)
        record = existing or PaperOrderRecord(
            portfolio_id=portfolio_id,
            instrument_id=instrument_id,
            idempotency_key=idempotency_key,
        )
        record.signal_id = signal_id
        record.side = result.fill.side.value
        record.order_type = "MARKET"
        record.status = OrderStatus.FILLED.value
        record.quantity = result.fill.quantity
        record.limit_price = None
        if existing is None:
            self._session.add(record)
        await self._session.flush()
        await self._record_fill(record.id, result.fill, reference_price)
        entries = [(quote_asset, result.cash_delta, "PAPER_REALIZED_PNL")]
        if result.position.instrument.value == "SPOT":
            entries.append((base_asset, -result.fill.quantity, "PAPER_POSITION"))
        await self._record_ledger(
            portfolio_id=portfolio_id,
            idempotency_key=f"paper-close:{idempotency_key}",
            occurred_at=result.fill.filled_at,
            entries=entries,
            details={
                "paper_order_id": str(record.id),
                "operation": "CLOSE",
                "reason": result.reason.value,
                "net_pnl": str(result.net_pnl),
            },
        )
        position_record = await self._session.scalar(
            select(PaperPositionRecord).where(
                PaperPositionRecord.position_key == result.position.position_id,
                PaperPositionRecord.status == "OPEN",
            )
        )
        if position_record is not None:
            position_record.status = "CLOSED"
            position_record.closed_at = result.fill.filled_at
            position_record.funding_pnl = result.position.funding_pnl
        await self._session.flush()
        return PaperPersistenceResult(record.id, True)

    async def _existing(self, idempotency_key: str) -> PaperOrderRecord | None:
        return cast(
            PaperOrderRecord | None,
            await self._session.scalar(
                select(PaperOrderRecord).where(PaperOrderRecord.idempotency_key == idempotency_key)
            ),
        )

    async def _record_fill(self, order_id: UUID, fill: Fill, reference_price: Decimal) -> None:
        fill_price = fill.price
        slippage = abs(fill_price - reference_price) / reference_price * Decimal("10000")
        self._session.add(
            PaperFillRecord(
                order_id=order_id,
                filled_at=fill.filled_at,
                quantity=fill.quantity,
                reference_price=reference_price,
                fill_price=fill_price,
                fee=fill.commission,
                slippage_bps=float(slippage),
            )
        )

    async def _record_ledger(
        self,
        *,
        portfolio_id: UUID,
        idempotency_key: str,
        occurred_at: datetime,
        entries: list[tuple[str, Decimal, str]],
        details: dict[str, str],
    ) -> None:
        existing = await self._session.scalar(
            select(PortfolioEvent.id).where(
                PortfolioEvent.portfolio_id == portfolio_id,
                PortfolioEvent.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return
        event = PortfolioEvent(
            portfolio_id=portfolio_id,
            event_type=PortfolioEventType.TRADE.value,
            occurred_at=occurred_at,
            recorded_at=datetime.now(UTC),
            source=EventSource.SYSTEM.value,
            idempotency_key=idempotency_key,
            details=details,
        )
        self._session.add(event)
        await self._session.flush()
        for asset, amount, kind in entries:
            if amount != 0:
                self._session.add(
                    LedgerEntry(
                        event_id=event.id,
                        asset=asset.upper(),
                        amount=amount,
                        entry_kind=kind,
                    )
                )
