from datetime import UTC, datetime, timedelta

from income_tg.operations.recovery import (
    RecoveryIssue,
    RecoveryPolicy,
    RecoveryState,
    check_recovery_state,
)

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def healthy_state(**overrides: object) -> RecoveryState:
    values: dict[str, object] = {
        "database_reachable": True,
        "schema_current": True,
        "latest_backup_at": NOW - timedelta(hours=1),
        "latest_backup_verified": True,
        "market_checkpoint_present": True,
        "model_pointer_present": True,
        "model_artifact_present": True,
        "bot_checkpoint_present": True,
        "ledger_consistent": True,
    }
    values.update(overrides)
    return RecoveryState(**values)  # type: ignore[arg-type]


def test_complete_fresh_state_is_recoverable() -> None:
    result = check_recovery_state(healthy_state(), now=NOW)

    assert result.recoverable
    assert result.issues == ()


def test_recovery_check_reports_every_failed_invariant() -> None:
    result = check_recovery_state(
        healthy_state(
            database_reachable=False,
            schema_current=False,
            latest_backup_at=NOW - timedelta(hours=27),
            latest_backup_verified=False,
            market_checkpoint_present=False,
            model_pointer_present=False,
            model_artifact_present=False,
            bot_checkpoint_present=False,
            ledger_consistent=False,
        ),
        now=NOW,
    )

    assert result.issues == (
        RecoveryIssue.DATABASE_UNREACHABLE,
        RecoveryIssue.SCHEMA_OUTDATED,
        RecoveryIssue.BACKUP_STALE,
        RecoveryIssue.BACKUP_UNVERIFIED,
        RecoveryIssue.MARKET_CHECKPOINT_MISSING,
        RecoveryIssue.MODEL_POINTER_MISSING,
        RecoveryIssue.MODEL_ARTIFACT_MISSING,
        RecoveryIssue.BOT_CHECKPOINT_MISSING,
        RecoveryIssue.LEDGER_INCONSISTENT,
    )


def test_missing_backup_is_reported_without_duplicate_unverified_issue() -> None:
    result = check_recovery_state(
        healthy_state(latest_backup_at=None, latest_backup_verified=False),
        now=NOW,
    )

    assert result.issues == (RecoveryIssue.BACKUP_MISSING,)


def test_optional_checkpoints_can_be_waived_for_clean_bootstrap() -> None:
    policy = RecoveryPolicy(
        require_market_checkpoint=False,
        require_model=False,
        require_bot_checkpoint=False,
    )
    result = check_recovery_state(
        healthy_state(
            market_checkpoint_present=False,
            model_pointer_present=False,
            model_artifact_present=False,
            bot_checkpoint_present=False,
        ),
        now=NOW,
        policy=policy,
    )

    assert result.recoverable


def test_future_dated_backup_is_not_accepted() -> None:
    result = check_recovery_state(
        healthy_state(latest_backup_at=NOW + timedelta(seconds=1)),
        now=NOW,
    )

    assert result.issues == (RecoveryIssue.BACKUP_STALE,)
