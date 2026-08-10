from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.common.enums import PortfolioKind, TradeSide
from income_tg.portfolio.bootstrap import initialize_owner
from income_tg.portfolio.errors import InsufficientBalanceError
from income_tg.portfolio.service import PortfolioService
from income_tg.storage.models import Portfolio, PortfolioEvent


async def _real_portfolio(session: AsyncSession) -> tuple[PortfolioService, Portfolio]:
    user = await initialize_owner(session, 42, Decimal("100000"))
    service = PortfolioService(session)
    portfolio = await service.get_portfolio(user.id, PortfolioKind.REAL_MANUAL)
    return service, portfolio


async def test_bootstrap_is_idempotent_and_funds_paper_portfolio(
    session: AsyncSession,
) -> None:
    first = await initialize_owner(session, 42, Decimal("100000"))
    second = await initialize_owner(session, 42, Decimal("100000"))
    assert first.id == second.id

    service = PortfolioService(session)
    paper = await service.get_portfolio(first.id, PortfolioKind.PAPER)
    assert await service.get_balances(paper.id) == {"RUB": Decimal("100000.000000000000000000")}

    event_count = await session.scalar(select(func.count()).select_from(PortfolioEvent))
    assert event_count == 1


async def test_deposit_is_idempotent(session: AsyncSession) -> None:
    service, portfolio = await _real_portfolio(session)
    await service.record_deposit(portfolio.id, "USDT", Decimal("1000"), "same-key")
    await service.record_deposit(portfolio.id, "USDT", Decimal("1000"), "same-key")

    assert await service.get_balances(portfolio.id) == {"USDT": Decimal("1000.000000000000000000")}


async def test_buy_creates_balanced_asset_movements(session: AsyncSession) -> None:
    service, portfolio = await _real_portfolio(session)
    await service.record_deposit(portfolio.id, "USDT", Decimal("1000"), "deposit")
    await service.record_trade(
        portfolio.id,
        TradeSide.BUY,
        "BTC",
        "USDT",
        Decimal("0.01"),
        Decimal("50000"),
        "buy-1",
        fee_amount=Decimal("1"),
    )

    assert await service.get_balances(portfolio.id) == {
        "BTC": Decimal("0.010000000000000000"),
        "USDT": Decimal("499.000000000000000000"),
    }


async def test_trade_rejects_insufficient_balance(session: AsyncSession) -> None:
    service, portfolio = await _real_portfolio(session)
    with pytest.raises(InsufficientBalanceError):
        await service.record_trade(
            portfolio.id,
            TradeSide.BUY,
            "BTC",
            "USDT",
            Decimal("0.01"),
            Decimal("50000"),
            "buy-without-cash",
        )


async def test_sell_and_withdrawal_reduce_assets(session: AsyncSession) -> None:
    service, portfolio = await _real_portfolio(session)
    await service.record_deposit(portfolio.id, "BTC", Decimal("1"), "deposit-btc")
    await service.record_trade(
        portfolio.id,
        TradeSide.SELL,
        "BTC",
        "USDT",
        Decimal("0.25"),
        Decimal("60000"),
        "sell-1",
        fee_amount=Decimal("10"),
    )
    await service.record_withdrawal(portfolio.id, "USDT", Decimal("1000"), "withdraw-1")

    assert await service.get_balances(portfolio.id) == {
        "BTC": Decimal("0.750000000000000000"),
        "USDT": Decimal("13990.000000000000000000"),
    }


async def test_reconcile_replaces_full_snapshot(session: AsyncSession) -> None:
    service, portfolio = await _real_portfolio(session)
    await service.record_deposit(portfolio.id, "BTC", Decimal("1"), "deposit-btc")
    await service.record_deposit(portfolio.id, "USDT", Decimal("10"), "deposit-usdt")

    await service.reconcile(
        portfolio.id,
        {"BTC": Decimal("0.5"), "RUB": Decimal("1000")},
        "snapshot-1",
    )

    assert await service.get_balances(portfolio.id) == {
        "BTC": Decimal("0.500000000000000000"),
        "RUB": Decimal("1000.000000000000000000"),
    }
