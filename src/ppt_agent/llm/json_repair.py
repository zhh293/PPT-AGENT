"""JSON repair utilities.

When LLMs return malformed JSON, these functions attempt to fix
common issues before falling back to error reporting.
"""

from __future__ import annotations

import json
import re


def extract_json_object(text: str) -> str | None:
    """Extract the first JSON object from a text string.

    Handles cases where the model wraps JSON in markdown code fences
    or adds explanatory text around it.
    """
    # Try markdown code fence first
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1).strip()
        if candidate.startswith("{") or candidate.startswith("["):
            return candidate

    # Try to find raw JSON object
    brace_start = text.find("{")
    if brace_start == -1:
        return None

    # Find matching closing brace by counting
    depth = 0
    in_string = False
    escape = False
    for i in range(brace_start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[brace_start : i + 1]

    # If we didn't find a matching brace, return everything from first {
    return text[brace_start:]


def repair_json(text: str, max_attempts: int = 2) -> tuple[dict | None, list[str]]:
    """Attempt to parse and repair JSON text.

    Returns (parsed_dict_or_None, list_of_warnings).
    """
    warnings: list[str] = []

    # Attempt 1: direct parse
    try:
        return json.loads(text), warnings
    except json.JSONDecodeError:
        pass

    # Extract JSON from surrounding text
    extracted = extract_json_object(text)
    if extracted:
        try:
            return json.loads(extracted), warnings
        except json.JSONDecodeError:
            pass
    else:
        warnings.append("No JSON object found in response")
        return None, warnings

    candidate = extracted

    for attempt in range(max_attempts):
        # Fix trailing commas before } or ]
        candidate = re.sub(r",\s*([}\]])", r"\1", candidate)

        # Fix single quotes -> double quotes (simple cases only)
        candidate = candidate.replace("'", '"')

        # Fix missing quotes on keys
        candidate = re.sub(
            r"(?<=[{,])\s*(\w+)\s*:",
            r' "\1":',
            candidate,
        )

        # Try to add missing closing braces
        open_braces = candidate.count("{") - candidate.count("}")
        if open_braces > 0:
            candidate = candidate + "}" * open_braces

        open_brackets = candidate.count("[") - candidate.count("]")
        if open_brackets > 0:
            candidate = candidate + "]" * open_brackets

        try:
            result = json.loads(candidate)
            warnings.append(f"JSON repaired on attempt {attempt + 1}")
            return result, warnings
        except json.JSONDecodeError:
            continue

    warnings.append(f"JSON repair failed after {max_attempts} attempts")
    return None, warnings
