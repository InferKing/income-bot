from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.portfolio.bootstrap import initialize_owner
from income_tg.risk.settings import RiskSettingsService
from income_tg.storage.trading_models import SettingsAuditRecord


async def test_risk_settings_are_initialized_and_audited(session: AsyncSession) -> None:
    user = await initialize_owner(session, 42, Decimal("100000"))
    service = RiskSettingsService(session)
    profile = await service.get(user.id)
    assert profile.max_leverage == 20
    await service.update(user.id, "max_leverage", "10")
    assert profile.max_leverage == 10
    assert await session.scalar(select(func.count()).select_from(SettingsAuditRecord)) == 1


async def test_risk_settings_reject_unsafe_leverage(session: AsyncSession) -> None:
    user = await initialize_owner(session, 42, Decimal("100000"))
    with pytest.raises(ValueError, match=r"1\.\.20"):
        await RiskSettingsService(session).update(user.id, "max_leverage", "25")
