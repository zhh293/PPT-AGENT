from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_CONTRACT_DIR = Path("specs/001-ppt-generation-agent/contracts")


class SchemaValidationError(ValueError):
    pass


def load_schema(name: str, contracts_dir: Path | str = DEFAULT_CONTRACT_DIR) -> dict[str, Any]:
    path = Path(contracts_dir) / name
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_all_schemas(contracts_dir: Path | str = DEFAULT_CONTRACT_DIR) -> dict[str, dict[str, Any]]:
    root = Path(contracts_dir)
    return {path.name: json.loads(path.read_text(encoding="utf-8")) for path in root.glob("*.schema.json")}


def validate_required(schema: dict[str, Any], payload: dict[str, Any], label: str = "payload") -> None:
    missing = [key for key in schema.get("required", []) if key not in payload]
    if missing:
        raise SchemaValidationError(f"{label} missing required keys: {', '.join(missing)}")
    for key, prop in schema.get("properties", {}).items():
        if key not in payload:
            continue
        value = payload[key]
        expected_type = prop.get("type")
        if expected_type == "array" and not isinstance(value, list):
            raise SchemaValidationError(f"{label}.{key} must be an array")
        if expected_type == "object" and not isinstance(value, dict):
            raise SchemaValidationError(f"{label}.{key} must be an object")
        if expected_type == "string" and not isinstance(value, str):
            raise SchemaValidationError(f"{label}.{key} must be a string")
        if expected_type == "integer" and not isinstance(value, int):
            raise SchemaValidationError(f"{label}.{key} must be an integer")
        if "enum" in prop and value not in prop["enum"]:
            raise SchemaValidationError(f"{label}.{key} must be one of {prop['enum']}")
