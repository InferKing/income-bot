from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.common.enums import PortfolioKind
from income_tg.paper_trading.engine import PaperExecutionEngine
from income_tg.paper_trading.models import (
    InstrumentKind,
    MarketSnapshot,
    Order,
    OrderSide,
    OrderType,
    PositionSide,
)
from income_tg.paper_trading.repository import PaperTradingRepository
from income_tg.portfolio.bootstrap import initialize_owner
from income_tg.portfolio.service import PortfolioService
from income_tg.storage.models import LedgerEntry
from income_tg.storage.trading_models import InstrumentRecord, PaperFillRecord, PaperOrderRecord


async def test_open_persistence_is_idempotent(session: AsyncSession) -> None:
    user = await initialize_owner(session, 90, Decimal("100000"))
    portfolio = await PortfolioService(session).get_portfolio(user.id, PortfolioKind.PAPER)
    instrument = InstrumentRecord(
        canonical_symbol="BTC/USDT",
        base_asset="BTC",
        quote_asset="USDT",
        market_type="linear_perpetual",
        is_active=True,
        metadata_json={},
    )
    session.add(instrument)
    await session.flush()
    now = datetime(2026, 8, 10, 12, tzinfo=UTC)
    market = MarketSnapshot(now, Decimal("99.9"), Decimal("100.1"), Decimal("99"), Decimal("101"))
    order = Order("idem-1", "BTCUSDT", OrderSide.BUY, OrderType.MARKET, Decimal("1"))
    opened = PaperExecutionEngine().open_position(
        position_id="p1",
        order=order,
        market=market,
        instrument=InstrumentKind.PERPETUAL,
        side=PositionSide.LONG,
        leverage=5,
        stop_loss=Decimal("98"),
        take_profit=Decimal("104"),
        available_cash=Decimal("100000"),
    )
    repository = PaperTradingRepository(session)

    reservation = await repository.reserve_order(
        portfolio_id=portfolio.id,
        instrument_id=instrument.id,
        order=order,
        idempotency_key="idem-1",
    )
    duplicate_reservation = await repository.reserve_order(
        portfolio_id=portfolio.id,
        instrument_id=instrument.id,
        order=order,
        idempotency_key="idem-1",
    )
    first = await repository.record_open(
        portfolio_id=portfolio.id,
        signal_id=None,
        instrument_id=instrument.id,
        order=order,
        result=opened,
        reference_price=Decimal("100"),
        idempotency_key="idem-1",
        base_asset="BTC",
    )
    second = await repository.record_open(
        portfolio_id=portfolio.id,
        signal_id=None,
        instrument_id=instrument.id,
        order=order,
        result=opened,
        reference_price=Decimal("100"),
        idempotency_key="idem-1",
        base_asset="BTC",
    )

    assert reservation.created and not duplicate_reservation.created
    assert first.created and not second.created
    assert first.order_id == second.order_id
    assert await session.scalar(select(func.count()).select_from(PaperOrderRecord)) == 1
    assert await session.scalar(select(func.count()).select_from(PaperFillRecord)) == 1
    assert (
        await session.scalar(
            select(func.count())
            .select_from(LedgerEntry)
            .where(LedgerEntry.entry_kind == "PAPER_MARGIN_AND_FEE")
        )
        == 1
    )
