from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
REQUIRED_SYMBOLS = frozenset({"BTCUSDT", "ETHUSDT", "TONUSDT"})


class PreflightIssue(StrEnum):
    ENVIRONMENT_NOT_PRODUCTION = "ENVIRONMENT_NOT_PRODUCTION"
    DATABASE_DSN_MISSING = "DATABASE_DSN_MISSING"
    BOT_TOKEN_MISSING = "BOT_TOKEN_MISSING"
    OWNER_ID_INVALID = "OWNER_ID_INVALID"
    LIVE_TRADING_ENABLED = "LIVE_TRADING_ENABLED"
    DEBUG_ENABLED = "DEBUG_ENABLED"
    BACKUP_DIRECTORY_INVALID = "BACKUP_DIRECTORY_INVALID"
    BACKUP_DIRECTORY_NOT_WRITABLE = "BACKUP_DIRECTORY_NOT_WRITABLE"
    MODEL_ARTIFACT_MISSING = "MODEL_ARTIFACT_MISSING"
    MARKET_REDUNDANCY_MISSING = "MARKET_REDUNDANCY_MISSING"
    REQUIRED_SYMBOLS_MISSING = "REQUIRED_SYMBOLS_MISSING"
    BACKUP_DSN_ENV_INVALID = "BACKUP_DSN_ENV_INVALID"
    BACKUP_DSN_MISSING = "BACKUP_DSN_MISSING"
    PG_DUMP_INVALID = "PG_DUMP_INVALID"


@dataclass(frozen=True, slots=True)
class PreflightConfig:
    environment: str
    database_dsn_present: bool
    bot_token_present: bool
    owner_telegram_id: int
    paper_trading_only: bool
    debug: bool
    backup_directory: Path
    model_artifact: Path
    market_sources: tuple[str, ...]
    symbols: tuple[str, ...]
    backup_dsn_environment_variable: str
    backup_dsn_present: bool
    pg_dump_executable: Path


@dataclass(frozen=True, slots=True)
class PreflightResult:
    passed: bool
    issues: tuple[PreflightIssue, ...]

    def __post_init__(self) -> None:
        if self.passed == bool(self.issues):
            raise ValueError("preflight decision and issues disagree")


def validate_production_preflight(config: PreflightConfig) -> PreflightResult:
    issues: list[PreflightIssue] = []
    if config.environment.casefold() != "production":
        issues.append(PreflightIssue.ENVIRONMENT_NOT_PRODUCTION)
    if not config.database_dsn_present:
        issues.append(PreflightIssue.DATABASE_DSN_MISSING)
    if not config.bot_token_present:
        issues.append(PreflightIssue.BOT_TOKEN_MISSING)
    if config.owner_telegram_id <= 0:
        issues.append(PreflightIssue.OWNER_ID_INVALID)
    if not config.paper_trading_only:
        issues.append(PreflightIssue.LIVE_TRADING_ENABLED)
    if config.debug:
        issues.append(PreflightIssue.DEBUG_ENABLED)

    if not _valid_data_directory(config.backup_directory):
        issues.append(PreflightIssue.BACKUP_DIRECTORY_INVALID)
    elif not os.access(config.backup_directory, os.W_OK):
        issues.append(PreflightIssue.BACKUP_DIRECTORY_NOT_WRITABLE)
    if not config.model_artifact.is_absolute() or not config.model_artifact.is_file():
        issues.append(PreflightIssue.MODEL_ARTIFACT_MISSING)
    sources = {item.strip().upper() for item in config.market_sources if item.strip()}
    if len(sources) < 2:
        issues.append(PreflightIssue.MARKET_REDUNDANCY_MISSING)
    symbols = {item.strip().upper() for item in config.symbols if item.strip()}
    if not REQUIRED_SYMBOLS <= symbols:
        issues.append(PreflightIssue.REQUIRED_SYMBOLS_MISSING)
    if not ENV_NAME_PATTERN.fullmatch(config.backup_dsn_environment_variable):
        issues.append(PreflightIssue.BACKUP_DSN_ENV_INVALID)
    elif not config.backup_dsn_present:
        issues.append(PreflightIssue.BACKUP_DSN_MISSING)
    if (
        not config.pg_dump_executable.is_absolute()
        or not config.pg_dump_executable.is_file()
        or not os.access(config.pg_dump_executable, os.X_OK)
    ):
        issues.append(PreflightIssue.PG_DUMP_INVALID)
    return PreflightResult(passed=not issues, issues=tuple(issues))


def _valid_data_directory(path: Path) -> bool:
    if not path.is_absolute() or not path.is_dir():
        return False
    resolved = path.resolve(strict=False)
    return resolved != Path(resolved.anchor)
