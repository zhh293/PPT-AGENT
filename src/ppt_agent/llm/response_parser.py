"""Response parsing utilities.

Extracts structured JSON from LLM responses, validates against
schemas, and handles repair/fallback flow.
"""

from __future__ import annotations

import json
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from ppt_agent.llm.json_repair import extract_json_object, repair_json
from ppt_agent.llm.messages import LLMResult


def parse_json_response(
    result: LLMResult,
    schema: dict | None = None,
) -> tuple[dict | None, list[str]]:
    """Parse a JSON response from an LLM result.

    If the result already has json_data (e.g., fake provider), use it.
    Otherwise, extract and repair JSON from the text.

    Returns (parsed_data, warnings).
    """
    warnings: list[str] = []

    if not result.success:
        warnings.append(f"LLM call failed: {result.error}")
        return None, warnings

    # Fake provider may pre-populate json_data
    if result.json_data is not None:
        data = result.json_data
    else:
        data, parse_warnings = repair_json(result.text)
        warnings.extend(parse_warnings)

    if data is None:
        return None, warnings

    # DeepSeek JSON Mode guarantees JSON syntax, not the nested business
    # contract. Enforce the complete schema locally before downstream code
    # receives the payload.
    if schema:
        schema_errors = validate_json_schema(data, schema)
        if schema_errors:
            warnings.extend(schema_errors)
            return None, warnings

    if result.repaired:
        warnings.append("Response required JSON repair")

    return data, warnings


def _format_json_path(path: Any) -> str:
    rendered = "$"
    for part in path:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += f".{part}"
    return rendered


def validate_json_schema(data: Any, schema: dict) -> list[str]:
    """Return deterministic, path-aware JSON Schema validation errors."""
    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
    except SchemaError as exc:
        return [f"Invalid JSON schema: {exc.message}"]

    errors = sorted(
        validator.iter_errors(data),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    return [
        f"Schema validation failed at "
        f"{_format_json_path(error.absolute_path)}: {error.message}"
        for error in errors
    ]
