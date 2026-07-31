"""LLM call audit logging.

Writes model_calls.jsonl with metadata about each LLM call.
Does NOT record full prompts or responses by default to avoid
storing sensitive content.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ppt_agent.llm.messages import LLMResult


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _response_shape(data: Any) -> dict:
    shape: dict[str, Any] = {"response_type": type(data).__name__}
    if isinstance(data, dict):
        shape["top_level_keys"] = sorted(str(key) for key in data)
        repairs = data.get("repairs")
        if "repairs" in data:
            shape["repairs_type"] = type(repairs).__name__
            if isinstance(repairs, list):
                shape["repair_count"] = len(repairs)
                shape["repair_item_types"] = [
                    type(item).__name__ for item in repairs
                ]
                shape["invalid_repair_indexes"] = [
                    index for index, item in enumerate(repairs)
                    if not isinstance(item, dict)
                ]
    return shape


def log_response_diagnostic(
    job_root: Path,
    phase: str,
    result: LLMResult,
    parsed_data: Any,
    warnings: list[str],
    *,
    stage: str,
    schema_valid: bool,
) -> None:
    """Log response structure when explicitly enabled for a debug run."""
    if not _env_enabled("PPT_AGENT_DEBUG_LLM"):
        return

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        "stage": stage,
        "provider": result.provider,
        "model": result.model,
        "response_hash": hashlib.sha256(result.text.encode()).hexdigest()[:16],
        "response_chars": len(result.text),
        "finish_reason": result.finish_reason,
        "reasoning_hash": (
            hashlib.sha256(result.reasoning_text.encode()).hexdigest()[:16]
            if result.reasoning_text else ""
        ),
        "reasoning_chars": len(result.reasoning_text),
        "json_repaired": result.repaired,
        "schema_valid": schema_valid,
        "validation_errors": warnings,
        **_response_shape(parsed_data),
    }
    if _env_enabled("PPT_AGENT_LOG_LLM_BODY"):
        record["response_text"] = result.text
        record["reasoning_text"] = result.reasoning_text

    log_path = Path(job_root) / "llm_diagnostics.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_model_call(
    job_root: Path,
    phase: str,
    result: LLMResult,
    prompt_summary: str = "",
    schema_name: str = "",
) -> None:
    """Append a model call record to model_calls.jsonl.

    Records provider, model, phase, latency, status, token usage,
    and a hash of the prompt (not the full text).
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        "provider": result.provider,
        "model": result.model,
        "prompt_hash": hashlib.sha256(prompt_summary.encode()).hexdigest()[:16] if prompt_summary else "",
        "schema_name": schema_name,
        "latency_ms": result.latency_ms,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "finish_reason": result.finish_reason,
        "reasoning_chars": len(result.reasoning_text),
        "status": "success" if result.success else "error",
        "error": result.error,
        "repaired": result.repaired,
    }

    log_path = Path(job_root) / "model_calls.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def log_llm_fallback(
    job_root: Path,
    phase: str,
    result: LLMResult,
) -> None:
    """Record that deterministic fallback replaced a failed model result."""
    timestamp = datetime.now(timezone.utc).isoformat()
    model_record = {
        "timestamp": timestamp,
        "phase": phase,
        "provider": result.provider,
        "model": result.model,
        "prompt_hash": "",
        "schema_name": "",
        "latency_ms": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "status": "fallback",
        "error": result.error,
        "repaired": False,
    }
    with (Path(job_root) / "model_calls.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(model_record, ensure_ascii=False) + "\n")

    history_record = {
        "event_id": f"evt_{uuid4().hex[:12]}",
        "job_id": Path(job_root).name,
        "phase": phase,
        "type": "llm_fallback",
        "timestamp": timestamp,
        "message": f"LLM failed; deterministic fallback used: {result.error}",
        "provider": result.provider,
        "model": result.model,
    }
    with (Path(job_root) / "history.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(history_record, ensure_ascii=False) + "\n")
