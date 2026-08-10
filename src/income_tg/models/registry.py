from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import joblib

from income_tg.models.inference import EnsembleModel


@dataclass(frozen=True, slots=True)
class RegisteredModel:
    version: str
    artifact_path: str
    sha256: str
    stage: str
    registered_at: str


class FileModelRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def register(self, model: EnsembleModel, *, stage: str = "CHALLENGER") -> RegisteredModel:
        if stage not in {"CHALLENGER", "CHAMPION", "RETIRED"}:
            raise ValueError("Неизвестная стадия модели")
        artifact = self.root / f"{model.version}.joblib"
        joblib.dump(model, artifact)
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        registered = RegisteredModel(
            version=model.version,
            artifact_path=str(artifact),
            sha256=digest,
            stage=stage,
            registered_at=datetime.now(UTC).isoformat(),
        )
        (self.root / f"{model.version}.json").write_text(
            json.dumps(asdict(registered), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return registered

    def load(self, version: str) -> EnsembleModel:
        metadata = self._metadata(version)
        artifact = Path(metadata.artifact_path)
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        if digest != metadata.sha256:
            raise ValueError("Контрольная сумма артефакта модели не совпадает")
        model = joblib.load(artifact)
        if not isinstance(model, EnsembleModel):
            raise TypeError("Артефакт не является EnsembleModel")
        return model

    def promote(self, version: str) -> RegisteredModel:
        challenger = self._metadata(version)
        promoted = RegisteredModel(
            version=challenger.version,
            artifact_path=challenger.artifact_path,
            sha256=challenger.sha256,
            stage="CHAMPION",
            registered_at=challenger.registered_at,
        )
        (self.root / f"{version}.json").write_text(
            json.dumps(asdict(promoted), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (self.root / "champion.json").write_text(
            json.dumps(asdict(promoted), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return promoted

    def load_champion(self) -> EnsembleModel:
        pointer = self.root / "champion.json"
        if not pointer.exists():
            raise FileNotFoundError("Champion-модель еще не назначена")
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        return self.load(str(payload["version"]))

    def describe(self, version: str) -> RegisteredModel:
        return self._metadata(version)

    def _metadata(self, version: str) -> RegisteredModel:
        path = self.root / f"{version}.json"
        if not path.exists():
            raise FileNotFoundError(f"Модель {version} не зарегистрирована")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return RegisteredModel(**payload)
