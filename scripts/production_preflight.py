from __future__ import annotations

import argparse
import os
from pathlib import Path

from income_tg.operations.preflight import PreflightConfig, validate_production_preflight


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate production configuration without logging secrets"
    )
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--model-artifact", type=Path, required=True)
    parser.add_argument("--pg-dump", type=Path, required=True)
    parser.add_argument("--backup-dsn-env", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    environment = os.environ
    owner = _integer(environment.get("INCOME_TG_TELEGRAM_OWNER_ID", ""))
    backup_dsn_name = str(args.backup_dsn_env)
    config = PreflightConfig(
        environment=environment.get("INCOME_TG_ENVIRONMENT", ""),
        database_dsn_present=bool(environment.get("INCOME_TG_DATABASE_URL")),
        bot_token_present=bool(environment.get("INCOME_TG_BOT_TOKEN")),
        owner_telegram_id=owner,
        paper_trading_only=_boolean(environment.get("INCOME_TG_PAPER_ONLY", "true")),
        debug=_boolean(environment.get("INCOME_TG_DEBUG", "false")),
        backup_directory=args.backup_dir,
        model_artifact=args.model_artifact,
        market_sources=_csv(environment.get("INCOME_TG_MARKET_SOURCES", "")),
        symbols=_csv(environment.get("INCOME_TG_SYMBOLS", "")),
        backup_dsn_environment_variable=backup_dsn_name,
        backup_dsn_present=bool(environment.get(backup_dsn_name)),
        pg_dump_executable=args.pg_dump,
    )
    result = validate_production_preflight(config)
    if result.passed:
        print("production_preflight=passed")
        return 0
    print("production_preflight=failed")
    for issue in result.issues:
        print(f"issue={issue.value}")
    return 1


def _boolean(value: str) -> bool:
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _integer(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


if __name__ == "__main__":
    raise SystemExit(main())
