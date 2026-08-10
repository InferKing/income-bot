import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from income_tg.operations.backup import (
    BackupArtifact,
    BackupCommandBuilder,
    RetentionPlanner,
    verify_backup_artifact,
)

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def artifact(root: Path, name: str, age_days: int) -> BackupArtifact:
    return BackupArtifact(path=root / name, created_at=NOW - timedelta(days=age_days))


def test_backup_command_passes_dsn_only_through_environment_binding(tmp_path: Path) -> None:
    pg_dump = (tmp_path / "bin" / "pg_dump").resolve()
    destination = (tmp_path / "backups" / "income-tg-20260810T120000Z.dump").resolve()
    secret = "postgresql://user:secret-password@db/income_tg"
    command = BackupCommandBuilder(
        pg_dump_executable=pg_dump,
        dsn_environment_variable="INCOME_TG_BACKUP_DATABASE_DSN",
    ).build(output_file=destination)

    assert all(secret not in argument for argument in command.argv)
    assert "secret-password" not in command.safe_display()
    assert "INCOME_TG_BACKUP_DATABASE_DSN" in command.safe_display()
    environment = command.materialize_environment(
        {"INCOME_TG_BACKUP_DATABASE_DSN": secret, "PATH": "safe"}
    )
    assert environment["PGDATABASE"] == secret
    assert environment["PATH"] == "safe"


def test_backup_command_requires_present_nonempty_dsn_environment(tmp_path: Path) -> None:
    command = BackupCommandBuilder(
        pg_dump_executable=(tmp_path / "pg_dump").resolve(),
        dsn_environment_variable="BACKUP_DSN",
    ).build(output_file=(tmp_path / "income-tg-20260810T120000Z.dump").resolve())

    with pytest.raises(ValueError, match="BACKUP_DSN"):
        command.materialize_environment({})


@pytest.mark.parametrize(
    "output",
    [
        Path("relative.dump"),
        Path("C:/tmp/arbitrary-name.dump")
        if Path.cwd().drive
        else Path("/tmp/arbitrary-name.dump"),
    ],
)
def test_backup_builder_rejects_nonexplicit_or_unmanaged_output_paths(output: Path) -> None:
    executable = (Path.cwd() / "pg_dump").resolve()
    builder = BackupCommandBuilder(pg_dump_executable=executable, dsn_environment_variable="DSN")

    with pytest.raises(ValueError):
        builder.build(output_file=output)


def test_retention_keeps_minimum_and_recent_then_plans_only_old_files(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    items = (
        artifact(root, "income-tg-20260810T120000Z.dump", 0),
        artifact(root, "income-tg-20260809T120000Z.dump", 1),
        artifact(root, "income-tg-20260808T120000Z.dump", 2),
        artifact(root, "income-tg-20260725T120000Z.dump", 16),
        artifact(root, "income-tg-20260720T120000Z.dump", 21),
    )

    plan = RetentionPlanner(
        backup_directory=root,
        retention=timedelta(days=14),
        minimum_to_keep=3,
    ).plan(items, now=NOW)

    assert tuple(item.path.name for item in plan.keep) == (
        "income-tg-20260810T120000Z.dump",
        "income-tg-20260809T120000Z.dump",
        "income-tg-20260808T120000Z.dump",
    )
    assert tuple(item.path.name for item in plan.delete_candidates) == (
        "income-tg-20260725T120000Z.dump",
        "income-tg-20260720T120000Z.dump",
    )
    assert all(not item.path.exists() for item in plan.delete_candidates)


def test_retention_does_not_delete_real_candidate(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    old_file = root / "income-tg-20260701T120000Z.dump"
    old_file.write_bytes(b"backup")
    item = BackupArtifact(path=old_file, created_at=NOW - timedelta(days=40))

    plan = RetentionPlanner(
        backup_directory=root,
        retention=timedelta(days=1),
        minimum_to_keep=1,
    ).plan(
        (
            artifact(root, "income-tg-20260810T120000Z.dump", 0),
            item,
        ),
        now=NOW,
    )

    assert plan.delete_candidates == (item,)
    assert old_file.read_bytes() == b"backup"


def test_retention_rejects_artifact_outside_root(tmp_path: Path) -> None:
    root = (tmp_path / "backups").resolve()
    root.mkdir()
    outside = (tmp_path / "income-tg-20260701T120000Z.dump").resolve()

    with pytest.raises(ValueError, match="escapes"):
        RetentionPlanner(backup_directory=root).plan(
            (BackupArtifact(path=outside, created_at=NOW),),
            now=NOW,
        )


def test_retention_rejects_filesystem_root() -> None:
    root = Path(Path.cwd().anchor)

    with pytest.raises(ValueError, match="root"):
        RetentionPlanner(backup_directory=root)


def test_backup_checksum_verification_detects_match_and_tampering(tmp_path: Path) -> None:
    backup = (tmp_path / "income-tg-20260810T120000Z.dump").resolve()
    checksum = backup.with_suffix(".dump.sha256")
    backup.write_bytes(b"known backup content")
    digest = hashlib.sha256(backup.read_bytes()).hexdigest()
    checksum.write_text(f"{digest}  {backup.name}\n", encoding="ascii")

    assert verify_backup_artifact(backup, checksum).verified
    backup.write_bytes(b"tampered")
    verification = verify_backup_artifact(backup, checksum)
    assert verification.backup_exists
    assert verification.checksum_exists
    assert not verification.checksum_matches
    assert not verification.verified


def test_missing_checksum_is_not_verified(tmp_path: Path) -> None:
    backup = (tmp_path / "income-tg-20260810T120000Z.dump").resolve()
    backup.write_bytes(b"backup")

    result = verify_backup_artifact(backup, backup.with_suffix(".dump.sha256"))

    assert result.backup_exists
    assert not result.checksum_exists
    assert not result.verified
