from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.common.enums import EventSource, PortfolioKind
from income_tg.portfolio.service import PortfolioService
from income_tg.storage.models import Portfolio, User
from income_tg.storage.trading_models import RiskProfileRecord


async def initialize_owner(
    session: AsyncSession,
    telegram_user_id: int,
    initial_paper_balance_rub: Decimal,
) -> User:
    if telegram_user_id <= 0:
        raise ValueError("telegram_user_id должен быть положительным")

    user = await session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
    if user is None:
        user = User(telegram_user_id=telegram_user_id)
        session.add(user)
        await session.flush()

    await _get_or_create_portfolio(
        session,
        user,
        kind=PortfolioKind.REAL_MANUAL,
        name="Crypto Wallet",
    )
    paper = await _get_or_create_portfolio(
        session,
        user,
        kind=PortfolioKind.PAPER,
        name="Виртуальный портфель",
    )
    await session.flush()

    risk_profile = await session.scalar(
        select(RiskProfileRecord).where(RiskProfileRecord.user_id == user.id)
    )
    if risk_profile is None:
        session.add(RiskProfileRecord(user_id=user.id))
        await session.flush()

    service = PortfolioService(session)
    await service.record_deposit(
        paper.id,
        "RUB",
        initial_paper_balance_rub,
        idempotency_key="bootstrap:paper-initial-balance:v1",
        source=EventSource.BOOTSTRAP,
    )
    return user


async def _get_or_create_portfolio(
    session: AsyncSession,
    user: User,
    *,
    kind: PortfolioKind,
    name: str,
) -> Portfolio:
    portfolio = await session.scalar(
        select(Portfolio).where(Portfolio.user_id == user.id, Portfolio.kind == kind.value)
    )
    if portfolio is None:
        portfolio = Portfolio(
            user_id=user.id,
            kind=kind.value,
            name=name,
            base_currency="RUB",
        )
        session.add(portfolio)
    return portfolio
