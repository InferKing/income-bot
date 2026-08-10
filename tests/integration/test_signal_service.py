from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.common.enums import PortfolioKind
from income_tg.portfolio.bootstrap import initialize_owner
from income_tg.portfolio.service import PortfolioService
from income_tg.risk.engine import RiskEngine
from income_tg.risk.models import MarketGuard, PortfolioRiskState, PositionDirection, SizingRequest
from income_tg.signals.domain import MarketType, SignalAction, SignalCandidate
from income_tg.signals.service import SignalService
from income_tg.storage.trading_models import (
    InstrumentRecord,
    NotificationOutboxRecord,
    RiskDecisionRecord,
)


async def test_approved_signal_and_notification_are_recorded_atomically(
    session: AsyncSession,
) -> None:
    user = await initialize_owner(session, 42, Decimal("100000"))
    portfolio = await PortfolioService(session).get_portfolio(user.id, PortfolioKind.PAPER)
    instrument = InstrumentRecord(
        canonical_symbol="BTC/USDT:PERP",
        base_asset="BTC",
        quote_asset="USDT",
        market_type="linear_perpetual",
        is_active=True,
        metadata_json={},
    )
    session.add(instrument)
    await session.flush()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    risk = RiskEngine().assess(
        SizingRequest(
            direction=PositionDirection.LONG,
            entry_price=Decimal("100"),
            stop_price=Decimal("98"),
            market=MarketGuard(observed_at=now, bid=Decimal("99.9"), ask=Decimal("100.1")),
            portfolio=PortfolioRiskState(
                equity=Decimal("100000"),
                available_cash=Decimal("100000"),
                day_start_equity=Decimal("100000"),
                peak_equity=Decimal("100000"),
                open_position_count=0,
            ),
        ),
        now=now,
    )
    signal = await SignalService(session).record(
        user_id=user.id,
        portfolio_id=portfolio.id,
        instrument_id=instrument.id,
        candidate=SignalCandidate(
            instrument="BTCUSDT",
            market_type=MarketType.LINEAR_PERPETUAL,
            action=SignalAction.LONG,
            confidence=0.8,
            reference_price=100,
            horizon="1h",
            as_of=now,
            valid_until=now + timedelta(hours=1),
            model_version="model-1",
            reasons=("рост открытого интереса",),
            cancel_condition="ниже 98",
        ),
        risk_decision=risk,
        stop_loss=Decimal("98"),
        take_profit=Decimal("104"),
        now=now,
    )
    assert signal.status == "APPROVED"
    assert await session.scalar(select(func.count()).select_from(RiskDecisionRecord)) == 1
    assert await session.scalar(select(func.count()).select_from(NotificationOutboxRecord)) == 1
