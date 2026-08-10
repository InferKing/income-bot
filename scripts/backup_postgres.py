from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from income_tg.operations.backup import (
    BACKUP_NAME_PATTERN,
    BackupArtifact,
    BackupCommandBuilder,
    RetentionPlanner,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a safe PostgreSQL custom-format backup")
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--pg-dump", type=Path, required=True)
    parser.add_argument("--dsn-env", required=True)
    parser.add_argument("--retention-days", type=int, default=14)
    parser.add_argument("--minimum-to-keep", type=int, default=3)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    now = datetime.now(UTC).replace(microsecond=0)
    if not args.backup_dir.is_absolute():
        print("backup_failed code=BACKUP_DIRECTORY_INVALID")
        return 2
    backup_directory = args.backup_dir.resolve(strict=False)
    if not backup_directory.is_dir():
        print("backup_failed code=BACKUP_DIRECTORY_INVALID")
        return 2
    destination = backup_directory / f"income-tg-{now:%Y%m%dT%H%M%SZ}.dump"
    if destination.exists():
        print("backup_failed code=DESTINATION_ALREADY_EXISTS")
        return 2

    try:
        planner = RetentionPlanner(
            backup_directory=backup_directory,
            retention=timedelta(days=int(args.retention_days)),
            minimum_to_keep=int(args.minimum_to_keep),
        )
        builder = BackupCommandBuilder(
            pg_dump_executable=args.pg_dump,
            dsn_environment_variable=args.dsn_env,
        )
        command = builder.build(output_file=destination)
        environment = command.materialize_environment(os.environ)
    except ValueError:
        print("backup_failed code=CONFIGURATION_INVALID")
        return 2

    try:
        completed = subprocess.run(
            command.argv,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        print("backup_failed code=PG_DUMP_NOT_EXECUTABLE")
        return 2
    if completed.returncode != 0:
        if destination.exists():
            destination.replace(destination.with_suffix(".failed"))
        print(f"backup_failed code=PG_DUMP_FAILED exit_code={completed.returncode}")
        return 1

    digest = _sha256(destination)
    destination.with_suffix(".dump.sha256").write_text(
        f"{digest}  {destination.name}\n",
        encoding="ascii",
    )
    print(f"backup_created file={destination.name} sha256={digest}")

    try:
        artifacts = _discover(backup_directory)
        plan = planner.plan(artifacts, now=now)
    except ValueError:
        print("retention_plan_failed code=RETENTION_CONFIGURATION_INVALID")
        return 2
    candidates = ",".join(item.path.name for item in plan.delete_candidates) or "none"
    print(f"retention_delete_candidates={candidates}")
    print("retention_note=no files were deleted")
    return 0


def _discover(directory: Path) -> tuple[BackupArtifact, ...]:
    result: list[BackupArtifact] = []
    for path in directory.glob("income-tg-*.dump"):
        if not BACKUP_NAME_PATTERN.fullmatch(path.name):
            continue
        timestamp = datetime.strptime(path.name, "income-tg-%Y%m%dT%H%M%SZ.dump").replace(
            tzinfo=UTC
        )
        result.append(BackupArtifact(path=path.resolve(), created_at=timestamp))
    return tuple(result)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
