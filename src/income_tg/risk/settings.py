from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.storage.trading_models import RiskProfileRecord, SettingsAuditRecord

DECIMAL_FIELDS = {
    "max_margin_fraction",
    "max_stop_risk_fraction",
    "max_daily_loss_fraction",
    "max_drawdown_fraction",
    "min_signal_confidence",
}
INTEGER_FIELDS = {"max_open_positions", "max_leverage"}


class RiskSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: UUID) -> RiskProfileRecord:
        profile = await self.session.scalar(
            select(RiskProfileRecord).where(RiskProfileRecord.user_id == user_id)
        )
        if profile is None:
            profile = RiskProfileRecord(user_id=user_id)
            self.session.add(profile)
            await self.session.flush()
        return profile

    async def update(
        self,
        user_id: UUID,
        setting_name: str,
        raw_value: str,
        *,
        source: str = "BOT",
    ) -> RiskProfileRecord:
        profile = await self.get(user_id)
        if setting_name in DECIMAL_FIELDS:
            decimal_value = Decimal(raw_value.replace(",", "."))
            if not decimal_value.is_finite() or not Decimal("0") < decimal_value <= Decimal("1"):
                raise ValueError("Доля должна находиться в диапазоне (0, 1]")
            if setting_name == "min_signal_confidence" and decimal_value <= Decimal("0.5"):
                raise ValueError("Порог уверенности должен быть больше 0.5")
            value: Decimal | int = decimal_value
        elif setting_name in INTEGER_FIELDS:
            integer_value = int(raw_value)
            if setting_name == "max_leverage" and not 1 <= integer_value <= 20:
                raise ValueError("Плечо должно находиться в диапазоне 1..20")
            if setting_name == "max_open_positions" and not 1 <= integer_value <= 20:
                raise ValueError("Количество позиций должно находиться в диапазоне 1..20")
            value = integer_value
        else:
            raise ValueError("Неизвестная настройка риска")
        old_value = getattr(profile, setting_name)
        setattr(profile, setting_name, value)
        self.session.add(
            SettingsAuditRecord(
                user_id=user_id,
                setting_name=setting_name,
                old_value=str(old_value),
                new_value=str(value),
                source=source,
            )
        )
        await self.session.flush()
        return profile
