from pathlib import Path

from income_tg.operations.preflight import (
    PreflightConfig,
    PreflightIssue,
    validate_production_preflight,
)


def valid_config(tmp_path: Path, **overrides: object) -> PreflightConfig:
    backup_directory = (tmp_path / "backups").resolve()
    backup_directory.mkdir(exist_ok=True)
    model_artifact = (tmp_path / "champion.json").resolve()
    model_artifact.write_text("{}", encoding="utf-8")
    pg_dump = (tmp_path / "pg_dump").resolve()
    pg_dump.write_bytes(b"executable placeholder")
    pg_dump.chmod(0o700)
    values: dict[str, object] = {
        "environment": "production",
        "database_dsn_present": True,
        "bot_token_present": True,
        "owner_telegram_id": 123456,
        "paper_trading_only": True,
        "debug": False,
        "backup_directory": backup_directory,
        "model_artifact": model_artifact,
        "market_sources": ("BYBIT", "OKX"),
        "symbols": ("BTCUSDT", "ETHUSDT", "TONUSDT"),
        "backup_dsn_environment_variable": "INCOME_TG_BACKUP_DATABASE_DSN",
        "backup_dsn_present": True,
        "pg_dump_executable": pg_dump,
    }
    values.update(overrides)
    return PreflightConfig(**values)  # type: ignore[arg-type]


def test_valid_production_configuration_passes(tmp_path: Path) -> None:
    result = validate_production_preflight(valid_config(tmp_path))

    assert result.passed
    assert result.issues == ()


def test_preflight_aggregates_safety_and_secret_presence_failures(tmp_path: Path) -> None:
    result = validate_production_preflight(
        valid_config(
            tmp_path,
            environment="development",
            database_dsn_present=False,
            bot_token_present=False,
            owner_telegram_id=0,
            paper_trading_only=False,
            debug=True,
            backup_dsn_environment_variable="invalid-name",
            backup_dsn_present=False,
        )
    )

    assert result.issues == (
        PreflightIssue.ENVIRONMENT_NOT_PRODUCTION,
        PreflightIssue.DATABASE_DSN_MISSING,
        PreflightIssue.BOT_TOKEN_MISSING,
        PreflightIssue.OWNER_ID_INVALID,
        PreflightIssue.LIVE_TRADING_ENABLED,
        PreflightIssue.DEBUG_ENABLED,
        PreflightIssue.BACKUP_DSN_ENV_INVALID,
    )


def test_preflight_requires_two_sources_and_all_mvp_symbols(tmp_path: Path) -> None:
    result = validate_production_preflight(
        valid_config(
            tmp_path,
            market_sources=("BYBIT", "bybit"),
            symbols=("BTCUSDT", "ETHUSDT"),
        )
    )

    assert result.issues == (
        PreflightIssue.MARKET_REDUNDANCY_MISSING,
        PreflightIssue.REQUIRED_SYMBOLS_MISSING,
    )


def test_preflight_does_not_expose_secret_values(tmp_path: Path) -> None:
    secret = "postgresql://user:secret@database/income_tg"
    config = valid_config(tmp_path)

    result = validate_production_preflight(config)

    assert secret not in repr(config)
    assert secret not in repr(result)


def test_preflight_rejects_missing_paths(tmp_path: Path) -> None:
    result = validate_production_preflight(
        valid_config(
            tmp_path,
            backup_directory=(tmp_path / "missing-backups").resolve(),
            model_artifact=(tmp_path / "missing-model.json").resolve(),
            pg_dump_executable=(tmp_path / "missing-pg-dump").resolve(),
        )
    )

    assert result.issues == (
        PreflightIssue.BACKUP_DIRECTORY_INVALID,
        PreflightIssue.MODEL_ARTIFACT_MISSING,
        PreflightIssue.PG_DUMP_INVALID,
    )
