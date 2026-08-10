from __future__ import annotations

import hashlib
import os
import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
BACKUP_NAME_PATTERN = re.compile(r"^income-tg-\d{8}T\d{6}Z\.dump$")


@dataclass(frozen=True, slots=True)
class EnvironmentBinding:
    target_name: str
    source_name: str

    def __post_init__(self) -> None:
        if not ENV_NAME_PATTERN.fullmatch(self.target_name):
            raise ValueError("invalid target environment variable name")
        if not ENV_NAME_PATTERN.fullmatch(self.source_name):
            raise ValueError("invalid source environment variable name")


@dataclass(frozen=True, slots=True)
class BackupCommand:
    argv: tuple[str, ...]
    environment_bindings: tuple[EnvironmentBinding, ...]
    output_file: Path

    def materialize_environment(self, source: Mapping[str, str]) -> dict[str, str]:
        environment = dict(source)
        for binding in self.environment_bindings:
            secret = source.get(binding.source_name)
            if not secret:
                raise ValueError(f"required environment variable is missing: {binding.source_name}")
            environment[binding.target_name] = secret
        return environment

    def safe_display(self) -> str:
        bindings = ", ".join(
            f"{item.target_name}=<from:{item.source_name}>" for item in self.environment_bindings
        )
        return f"{shlex.join(self.argv)} env[{bindings}]"


class BackupCommandBuilder:
    def __init__(self, *, pg_dump_executable: Path, dsn_environment_variable: str) -> None:
        self._pg_dump = _absolute_path(pg_dump_executable, "pg_dump_executable")
        if not ENV_NAME_PATTERN.fullmatch(dsn_environment_variable):
            raise ValueError("invalid DSN environment variable name")
        self._dsn_environment_variable = dsn_environment_variable

    def build(self, *, output_file: Path) -> BackupCommand:
        destination = _absolute_path(output_file, "output_file")
        if destination.suffix != ".dump":
            raise ValueError("backup output must use .dump suffix")
        if not BACKUP_NAME_PATTERN.fullmatch(destination.name):
            raise ValueError("backup filename must use income-tg-YYYYMMDDTHHMMSSZ.dump format")
        return BackupCommand(
            argv=(
                str(self._pg_dump),
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                f"--file={destination}",
            ),
            environment_bindings=(
                EnvironmentBinding(
                    target_name="PGDATABASE",
                    source_name=self._dsn_environment_variable,
                ),
            ),
            output_file=destination,
        )


@dataclass(frozen=True, slots=True)
class BackupArtifact:
    path: Path
    created_at: datetime

    def __post_init__(self) -> None:
        _aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class RetentionPlan:
    keep: tuple[BackupArtifact, ...]
    delete_candidates: tuple[BackupArtifact, ...]


class RetentionPlanner:
    """Produces an explicit deletion plan; it never removes files."""

    def __init__(
        self,
        *,
        backup_directory: Path,
        retention: timedelta = timedelta(days=14),
        minimum_to_keep: int = 3,
    ) -> None:
        self._root = _safe_directory(backup_directory)
        if retention <= timedelta(0):
            raise ValueError("retention must be positive")
        if minimum_to_keep < 1:
            raise ValueError("minimum_to_keep must be positive")
        self._retention = retention
        self._minimum_to_keep = minimum_to_keep

    def plan(
        self,
        artifacts: tuple[BackupArtifact, ...],
        *,
        now: datetime,
    ) -> RetentionPlan:
        _aware(now, "now")
        unique_paths: set[Path] = set()
        checked: list[BackupArtifact] = []
        for artifact in artifacts:
            resolved = _absolute_path(artifact.path, "artifact path")
            if not resolved.is_relative_to(self._root):
                raise ValueError("backup artifact escapes configured backup directory")
            if not BACKUP_NAME_PATTERN.fullmatch(resolved.name):
                raise ValueError("retention accepts only managed income-tg dump files")
            if resolved in unique_paths:
                raise ValueError("duplicate backup artifact")
            unique_paths.add(resolved)
            checked.append(BackupArtifact(path=resolved, created_at=artifact.created_at))

        newest_first = sorted(checked, key=lambda item: item.created_at, reverse=True)
        keep: list[BackupArtifact] = []
        delete: list[BackupArtifact] = []
        for index, artifact in enumerate(newest_first):
            age = now - artifact.created_at
            if age < timedelta(0):
                keep.append(artifact)
            elif index < self._minimum_to_keep or age <= self._retention:
                keep.append(artifact)
            else:
                delete.append(artifact)
        return RetentionPlan(keep=tuple(keep), delete_candidates=tuple(delete))


@dataclass(frozen=True, slots=True)
class BackupVerification:
    backup_exists: bool
    checksum_exists: bool
    checksum_matches: bool

    @property
    def verified(self) -> bool:
        return self.backup_exists and self.checksum_exists and self.checksum_matches


def verify_backup_artifact(backup_file: Path, checksum_file: Path) -> BackupVerification:
    backup = _absolute_path(backup_file, "backup_file")
    checksum = _absolute_path(checksum_file, "checksum_file")
    backup_exists = backup.is_file()
    checksum_exists = checksum.is_file()
    if not backup_exists or not checksum_exists:
        return BackupVerification(backup_exists, checksum_exists, False)
    expected = checksum.read_text(encoding="ascii").strip().split(maxsplit=1)[0].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        return BackupVerification(True, True, False)
    return BackupVerification(True, True, _sha256(backup) == expected)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _absolute_path(value: Path, name: str) -> Path:
    if not value.is_absolute():
        raise ValueError(f"{name} must be absolute")
    return value.resolve(strict=False)


def _safe_directory(value: Path) -> Path:
    directory = _absolute_path(value, "backup_directory")
    if directory == Path(directory.anchor):
        raise ValueError("filesystem root cannot be used as backup directory")
    return directory


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def materialize_backup_environment(command: BackupCommand) -> dict[str, str]:
    """Materialize secrets only at the subprocess boundary."""
    return command.materialize_environment(os.environ)
