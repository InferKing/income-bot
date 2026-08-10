from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="INCOME_TG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: SecretStr | None = None
    telegram_owner_id: int = Field(default=0, ge=0)
    database_url: str = "postgresql+asyncpg://income_tg:income_tg@localhost:5432/income_tg"
    log_level: str = "INFO"
    manual_usdt_rub_rate: Decimal = Field(default=Decimal("0"), ge=0)
    initial_paper_balance_rub: Decimal = Field(default=Decimal("100000"), gt=0)
    environment: str = "development"
    paper_only: bool = True
    debug: bool = False
    market_sources: str = "BYBIT,OKX"
    symbols: str = "BTCUSDT,ETHUSDT,TONUSDT"

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()

    def require_bot_token(self) -> str:
        if self.bot_token is None or not self.bot_token.get_secret_value():
            raise ValueError("INCOME_TG_BOT_TOKEN is required to start the bot")
        return self.bot_token.get_secret_value()

    def require_owner_id(self) -> int:
        if self.telegram_owner_id <= 0:
            raise ValueError("INCOME_TG_TELEGRAM_OWNER_ID must be a positive integer")
        return self.telegram_owner_id


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
