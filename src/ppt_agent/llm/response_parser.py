"""Response parsing utilities.

Extracts structured JSON from LLM responses, validates against
schemas, and handles repair/fallback flow.
"""

from __future__ import annotations

import json
from typing import Any

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

    # Schema validation (lightweight)
    if schema:
        schema_warnings = _validate_required_keys(data, schema)
        warnings.extend(schema_warnings)

    if result.repaired:
        warnings.append("Response required JSON repair")

    return data, warnings


def _validate_required_keys(data: dict, schema: dict) -> list[str]:
    """Check that required keys from a JSON schema are present."""
    warnings: list[str] = []
    required = schema.get("required", [])
    properties = schema.get("properties", {})

    for key in required:
        if key not in data:
            warnings.append(f"Missing required field: {key}")

    return warnings
