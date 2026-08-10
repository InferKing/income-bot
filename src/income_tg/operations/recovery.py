from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class RecoveryIssue(StrEnum):
    DATABASE_UNREACHABLE = "DATABASE_UNREACHABLE"
    SCHEMA_OUTDATED = "SCHEMA_OUTDATED"
    BACKUP_MISSING = "BACKUP_MISSING"
    BACKUP_STALE = "BACKUP_STALE"
    BACKUP_UNVERIFIED = "BACKUP_UNVERIFIED"
    MARKET_CHECKPOINT_MISSING = "MARKET_CHECKPOINT_MISSING"
    MODEL_POINTER_MISSING = "MODEL_POINTER_MISSING"
    MODEL_ARTIFACT_MISSING = "MODEL_ARTIFACT_MISSING"
    BOT_CHECKPOINT_MISSING = "BOT_CHECKPOINT_MISSING"
    LEDGER_INCONSISTENT = "LEDGER_INCONSISTENT"


@dataclass(frozen=True, slots=True)
class RecoveryState:
    database_reachable: bool
    schema_current: bool
    latest_backup_at: datetime | None
    latest_backup_verified: bool
    market_checkpoint_present: bool
    model_pointer_present: bool
    model_artifact_present: bool
    bot_checkpoint_present: bool
    ledger_consistent: bool

    def __post_init__(self) -> None:
        if self.latest_backup_at is not None:
            _aware(self.latest_backup_at, "latest_backup_at")


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    maximum_backup_age: timedelta = timedelta(hours=26)
    require_market_checkpoint: bool = True
    require_model: bool = True
    require_bot_checkpoint: bool = True

    def __post_init__(self) -> None:
        if self.maximum_backup_age <= timedelta(0):
            raise ValueError("maximum_backup_age must be positive")


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    recoverable: bool
    issues: tuple[RecoveryIssue, ...]

    def __post_init__(self) -> None:
        if self.recoverable == bool(self.issues):
            raise ValueError("recovery decision and issues disagree")


def check_recovery_state(
    state: RecoveryState,
    *,
    now: datetime,
    policy: RecoveryPolicy | None = None,
) -> RecoveryResult:
    _aware(now, "now")
    active = policy or RecoveryPolicy()
    issues: list[RecoveryIssue] = []
    if not state.database_reachable:
        issues.append(RecoveryIssue.DATABASE_UNREACHABLE)
    if not state.schema_current:
        issues.append(RecoveryIssue.SCHEMA_OUTDATED)
    if state.latest_backup_at is None:
        issues.append(RecoveryIssue.BACKUP_MISSING)
    else:
        backup_age = now - state.latest_backup_at
        if backup_age < timedelta(0) or backup_age > active.maximum_backup_age:
            issues.append(RecoveryIssue.BACKUP_STALE)
        if not state.latest_backup_verified:
            issues.append(RecoveryIssue.BACKUP_UNVERIFIED)
    if active.require_market_checkpoint and not state.market_checkpoint_present:
        issues.append(RecoveryIssue.MARKET_CHECKPOINT_MISSING)
    if active.require_model:
        if not state.model_pointer_present:
            issues.append(RecoveryIssue.MODEL_POINTER_MISSING)
        if not state.model_artifact_present:
            issues.append(RecoveryIssue.MODEL_ARTIFACT_MISSING)
    if active.require_bot_checkpoint and not state.bot_checkpoint_present:
        issues.append(RecoveryIssue.BOT_CHECKPOINT_MISSING)
    if not state.ledger_consistent:
        issues.append(RecoveryIssue.LEDGER_INCONSISTENT)
    return RecoveryResult(recoverable=not issues, issues=tuple(issues))


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
