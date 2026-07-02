from __future__ import annotations

from pathlib import Path
from typing import Any

from ppt_agent.models.artifacts import atomic_write_json, read_json
from ppt_agent.models.schema_loader import load_schema, validate_required


def write_validated_artifact(path: Path, payload: dict[str, Any], schema_name: str, contracts_dir: Path | str) -> None:
    validate_required(load_schema(schema_name, contracts_dir), payload, path.name)
    atomic_write_json(path, payload)


def read_artifact(path: Path) -> dict[str, Any]:
    return read_json(path)
