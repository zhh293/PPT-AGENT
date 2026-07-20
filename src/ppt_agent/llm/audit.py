"""LLM call audit logging.

Writes model_calls.jsonl with metadata about each LLM call.
Does NOT record full prompts or responses by default to avoid
storing sensitive content.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ppt_agent.llm.messages import LLMResult


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
