import json

import pytest

from income_tg.jobs import ActivationReceipt, FileModelActivator
from income_tg.models.registry import FileModelRegistry, RegisteredModel


def write_metadata(registry: FileModelRegistry, model: RegisteredModel) -> None:
    payload = {
        "version": model.version,
        "artifact_path": model.artifact_path,
        "sha256": model.sha256,
        "stage": model.stage,
        "registered_at": model.registered_at,
    }
    (registry.root / f"{model.version}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_file_activator_switches_pointer_and_can_roll_back(tmp_path) -> None:
    registry = FileModelRegistry(tmp_path)
    v1 = RegisteredModel("v1", "one", "hash1", "CHAMPION", "then")
    v2 = RegisteredModel("v2", "two", "hash2", "CHALLENGER", "now")
    write_metadata(registry, v1)
    write_metadata(registry, v2)
    (registry.root / "champion.json").write_text(
        (registry.root / "v1.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    activator = FileModelActivator(registry)

    receipt = activator.activate("v2")
    assert receipt == ActivationReceipt("v2", "v1")
    assert json.loads((registry.root / "champion.json").read_text())["version"] == "v2"
    assert json.loads((registry.root / "v1.json").read_text())["stage"] == "RETIRED"

    activator.rollback(receipt)
    assert json.loads((registry.root / "champion.json").read_text())["version"] == "v1"
    assert json.loads((registry.root / "v2.json").read_text())["stage"] == "RETIRED"


def test_rollback_refuses_to_overwrite_a_newer_activation(tmp_path) -> None:
    registry = FileModelRegistry(tmp_path)
    for version in ("v1", "v2", "v3"):
        write_metadata(
            registry,
            RegisteredModel(version, version, "hash", "CHALLENGER", "now"),
        )
    activator = FileModelActivator(registry)
    first = activator.activate("v1")
    activator.activate("v2")

    with pytest.raises(RuntimeError, match="unsafe rollback"):
        activator.rollback(first)
