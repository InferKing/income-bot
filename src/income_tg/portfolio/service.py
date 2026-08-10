from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from income_tg.common.enums import (
    EventSource,
    PortfolioEventType,
    PortfolioKind,
    TradeSide,
)
from income_tg.common.money import normalize_asset, parse_positive_decimal
from income_tg.common.time import utc_now
from income_tg.portfolio.errors import InsufficientBalanceError, PortfolioNotFoundError
from income_tg.portfolio.schemas import PortfolioBalance
from income_tg.storage.models import LedgerEntry, Portfolio, PortfolioEvent, User


class PortfolioService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_owner(self, telegram_user_id: int) -> User | None:
        return cast(
            User | None,
            await self.session.scalar(
                select(User).where(User.telegram_user_id == telegram_user_id)
            ),
        )

    async def get_portfolio(self, user_id: UUID, kind: PortfolioKind) -> Portfolio:
        portfolio = await self.session.scalar(
            select(Portfolio).where(Portfolio.user_id == user_id, Portfolio.kind == kind.value)
        )
        if portfolio is None:
            raise PortfolioNotFoundError(f"Портфель {kind.value} не найден")
        return portfolio

    async def list_balances(self, user_id: UUID) -> list[PortfolioBalance]:
        portfolios = list(
            await self.session.scalars(
                select(Portfolio).where(Portfolio.user_id == user_id).order_by(Portfolio.kind)
            )
        )
        result: list[PortfolioBalance] = []
        for portfolio in portfolios:
            balances = await self.get_balances(portfolio.id)
            result.append(
                PortfolioBalance(
                    portfolio_id=portfolio.id,
                    name=portfolio.name,
                    kind=portfolio.kind,
                    balances=balances,
                )
            )
        return result

    async def get_balances(self, portfolio_id: UUID) -> dict[str, Decimal]:
        statement = (
            select(LedgerEntry.asset, func.sum(LedgerEntry.amount))
            .join(PortfolioEvent, PortfolioEvent.id == LedgerEntry.event_id)
            .where(PortfolioEvent.portfolio_id == portfolio_id)
            .group_by(LedgerEntry.asset)
            .order_by(LedgerEntry.asset)
        )
        rows = (await self.session.execute(statement)).all()
        return {asset: Decimal(total) for asset, total in rows if Decimal(total) != 0}

    async def record_deposit(
        self,
        portfolio_id: UUID,
        asset: str,
        amount: Decimal,
        idempotency_key: str,
        *,
        source: EventSource = EventSource.BOT,
        occurred_at: datetime | None = None,
    ) -> PortfolioEvent:
        normalized_asset = normalize_asset(asset)
        positive_amount = parse_positive_decimal(amount)
        return await self._record_event(
            portfolio_id=portfolio_id,
            event_type=PortfolioEventType.DEPOSIT,
            entries=[(normalized_asset, positive_amount, "PRINCIPAL")],
            idempotency_key=idempotency_key,
            source=source,
            occurred_at=occurred_at,
            details={"asset": normalized_asset, "amount": str(positive_amount)},
        )

    async def record_withdrawal(
        self,
        portfolio_id: UUID,
        asset: str,
        amount: Decimal,
        idempotency_key: str,
        *,
        source: EventSource = EventSource.BOT,
        occurred_at: datetime | None = None,
    ) -> PortfolioEvent:
        normalized_asset = normalize_asset(asset)
        positive_amount = parse_positive_decimal(amount)
        await self._require_balance(portfolio_id, normalized_asset, positive_amount)
        return await self._record_event(
            portfolio_id=portfolio_id,
            event_type=PortfolioEventType.WITHDRAWAL,
            entries=[(normalized_asset, -positive_amount, "PRINCIPAL")],
            idempotency_key=idempotency_key,
            source=source,
            occurred_at=occurred_at,
            details={"asset": normalized_asset, "amount": str(positive_amount)},
        )

    async def record_trade(
        self,
        portfolio_id: UUID,
        side: TradeSide,
        base_asset: str,
        quote_asset: str,
        quantity: Decimal,
        price: Decimal,
        idempotency_key: str,
        *,
        fee_amount: Decimal = Decimal("0"),
        fee_asset: str | None = None,
        source: EventSource = EventSource.BOT,
        occurred_at: datetime | None = None,
    ) -> PortfolioEvent:
        base = normalize_asset(base_asset)
        quote = normalize_asset(quote_asset)
        if base == quote:
            raise ValueError("Базовый и котируемый активы должны отличаться")
        qty = parse_positive_decimal(quantity, "quantity")
        trade_price = parse_positive_decimal(price, "price")
        fee = Decimal(fee_amount)
        if not fee.is_finite() or fee < 0:
            raise ValueError("fee_amount не может быть отрицательным")
        normalized_fee_asset = normalize_asset(fee_asset or quote)
        quote_amount = qty * trade_price

        entries: list[tuple[str, Decimal, str]]
        if side is TradeSide.BUY:
            await self._require_balances(
                portfolio_id,
                self._aggregate_required([(quote, quote_amount), (normalized_fee_asset, fee)]),
            )
            entries = [
                (base, qty, "TRADE_BASE"),
                (quote, -quote_amount, "TRADE_QUOTE"),
            ]
        else:
            sell_requirements: list[tuple[str, Decimal]] = [(base, qty)]
            if normalized_fee_asset == quote:
                sell_requirements.append((quote, max(fee - quote_amount, Decimal("0"))))
            else:
                sell_requirements.append((normalized_fee_asset, fee))
            await self._require_balances(
                portfolio_id,
                self._aggregate_required(sell_requirements),
            )
            entries = [
                (base, -qty, "TRADE_BASE"),
                (quote, quote_amount, "TRADE_QUOTE"),
            ]
        if fee > 0:
            entries.append((normalized_fee_asset, -fee, "FEE"))

        return await self._record_event(
            portfolio_id=portfolio_id,
            event_type=PortfolioEventType.TRADE,
            entries=entries,
            idempotency_key=idempotency_key,
            source=source,
            occurred_at=occurred_at,
            details={
                "side": side.value,
                "base_asset": base,
                "quote_asset": quote,
                "quantity": str(qty),
                "price": str(trade_price),
                "fee_amount": str(fee),
                "fee_asset": normalized_fee_asset,
            },
        )

    async def reconcile(
        self,
        portfolio_id: UUID,
        target_balances: Mapping[str, Decimal],
        idempotency_key: str,
        *,
        source: EventSource = EventSource.BOT,
        occurred_at: datetime | None = None,
    ) -> PortfolioEvent:
        normalized_targets: dict[str, Decimal] = {}
        for asset, raw_amount in target_balances.items():
            normalized_asset = normalize_asset(asset)
            amount = Decimal(raw_amount)
            if not amount.is_finite() or amount < 0:
                raise ValueError(f"Остаток {normalized_asset} не может быть отрицательным")
            normalized_targets[normalized_asset] = amount

        current = await self.get_balances(portfolio_id)
        assets = set(current) | set(normalized_targets)
        entries = [
            (
                asset,
                normalized_targets.get(asset, Decimal("0")) - current.get(asset, Decimal("0")),
                "RECONCILIATION",
            )
            for asset in sorted(assets)
            if normalized_targets.get(asset, Decimal("0")) != current.get(asset, Decimal("0"))
        ]
        return await self._record_event(
            portfolio_id=portfolio_id,
            event_type=PortfolioEventType.ADJUSTMENT,
            entries=entries,
            idempotency_key=idempotency_key,
            source=source,
            occurred_at=occurred_at,
            details={
                "target_balances": {key: str(value) for key, value in normalized_targets.items()},
                "previous_balances": {key: str(value) for key, value in current.items()},
            },
        )

    async def get_event(self, event_id: UUID) -> PortfolioEvent | None:
        return cast(
            PortfolioEvent | None,
            await self.session.scalar(
                select(PortfolioEvent)
                .options(selectinload(PortfolioEvent.entries))
                .where(PortfolioEvent.id == event_id)
            ),
        )

    async def _record_event(
        self,
        *,
        portfolio_id: UUID,
        event_type: PortfolioEventType,
        entries: Sequence[tuple[str, Decimal, str]],
        idempotency_key: str,
        source: EventSource,
        occurred_at: datetime | None,
        details: dict[str, Any],
    ) -> PortfolioEvent:
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("Некорректный idempotency_key")
        existing = await self.session.scalar(
            select(PortfolioEvent)
            .options(selectinload(PortfolioEvent.entries))
            .where(
                PortfolioEvent.portfolio_id == portfolio_id,
                PortfolioEvent.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing

        await self._require_portfolio(portfolio_id)
        event = PortfolioEvent(
            portfolio_id=portfolio_id,
            event_type=event_type.value,
            occurred_at=occurred_at or utc_now(),
            source=source.value,
            idempotency_key=idempotency_key,
            details=details,
        )
        self.session.add(event)
        await self.session.flush()
        for asset, amount, entry_kind in entries:
            if amount == 0:
                continue
            self.session.add(
                LedgerEntry(
                    event_id=event.id,
                    asset=normalize_asset(asset),
                    amount=amount,
                    entry_kind=entry_kind,
                )
            )
        await self.session.flush()
        return event

    async def _require_portfolio(self, portfolio_id: UUID) -> None:
        if (
            await self.session.scalar(select(Portfolio.id).where(Portfolio.id == portfolio_id))
            is None
        ):
            raise PortfolioNotFoundError("Портфель не найден")

    async def _require_balance(self, portfolio_id: UUID, asset: str, required: Decimal) -> None:
        await self._require_balances(portfolio_id, {asset: required})

    async def _require_balances(self, portfolio_id: UUID, required: Mapping[str, Decimal]) -> None:
        current = await self.get_balances(portfolio_id)
        for asset, amount in required.items():
            if amount > 0 and current.get(asset, Decimal("0")) < amount:
                available = current.get(asset, Decimal("0"))
                raise InsufficientBalanceError(
                    f"Недостаточно {asset}: доступно {available}, требуется {amount}"
                )

    @staticmethod
    def _aggregate_required(items: Sequence[tuple[str, Decimal]]) -> dict[str, Decimal]:
        result: dict[str, Decimal] = {}
        for asset, amount in items:
            result[asset] = result.get(asset, Decimal("0")) + amount
        return result


def portfolio_by_kind_statement(user_id: UUID, kind: PortfolioKind) -> Select[tuple[Portfolio]]:
    return select(Portfolio).where(Portfolio.user_id == user_id, Portfolio.kind == kind.value)
