from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from income_tg.models.registry import FileModelRegistry, RegisteredModel


@dataclass(frozen=True, slots=True)
class ActivationReceipt:
    activated_version: str
    previous_version: str | None


class AtomicModelActivator(Protocol):
    def activate(self, version: str) -> ActivationReceipt:
        """Atomically switch the active-model pointer and return rollback information."""

    def rollback(self, receipt: ActivationReceipt) -> None:
        """Restore the pointer captured by an activation receipt."""


class FileModelActivator:
    """Atomic champion-pointer updates compatible with ``FileModelRegistry``."""

    def __init__(self, registry: FileModelRegistry) -> None:
        self.registry = registry

    def activate(self, version: str) -> ActivationReceipt:
        metadata = self._metadata(version)
        previous = self._current_version()
        promoted = RegisteredModel(
            version=metadata.version,
            artifact_path=metadata.artifact_path,
            sha256=metadata.sha256,
            stage="CHAMPION",
            registered_at=metadata.registered_at,
        )
        self._atomic_json(self.registry.root / f"{version}.json", asdict(promoted))
        self._atomic_json(self.registry.root / "champion.json", asdict(promoted))
        if previous is not None and previous != version:
            self._set_stage(previous, "RETIRED")
        return ActivationReceipt(version, previous)

    def rollback(self, receipt: ActivationReceipt) -> None:
        if self._current_version() != receipt.activated_version:
            raise RuntimeError("active model changed after activation; refusing unsafe rollback")
        if receipt.previous_version is None:
            pointer = self.registry.root / "champion.json"
            if pointer.exists():
                pointer.unlink()
            self._set_stage(receipt.activated_version, "CHALLENGER")
            return
        self.activate(receipt.previous_version)

    def _set_stage(self, version: str, stage: str) -> None:
        metadata = self._metadata(version)
        updated = RegisteredModel(
            version=metadata.version,
            artifact_path=metadata.artifact_path,
            sha256=metadata.sha256,
            stage=stage,
            registered_at=metadata.registered_at,
        )
        self._atomic_json(self.registry.root / f"{version}.json", asdict(updated))

    def _metadata(self, version: str) -> RegisteredModel:
        path = self.registry.root / f"{version}.json"
        if not path.exists():
            raise FileNotFoundError(f"model is not registered: {version}")
        return RegisteredModel(**json.loads(path.read_text(encoding="utf-8")))

    def _current_version(self) -> str | None:
        pointer = self.registry.root / "champion.json"
        if not pointer.exists():
            return None
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        return str(payload["version"])

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, object]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
