"""Dream task for cross-session memory consolidation.

After a job completes, the dream task reviews the session log and
extracts durable learnings into memory.md (Layer 2). Only insights
above a relevance threshold are preserved.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from ppt_agent.context.memory_layers import append_memory, read_memory

logger = logging.getLogger(__name__)

RELEVANCE_THRESHOLD = 0.6


_HISTORY_TAIL_LINES = 20


def _read_history_tail(workspace_root: Path, max_lines: int = _HISTORY_TAIL_LINES) -> str:
    """Read the last *max_lines* entries from history.jsonl, if it exists."""
    history_path = workspace_root / "history.jsonl"
    if not history_path.exists():
        return ""
    try:
        lines = history_path.read_text(encoding="utf-8").splitlines()
        tail = lines[-max_lines:] if len(lines) > max_lines else lines
        return "\n".join(tail)
    except Exception as exc:
        logger.warning("Failed to read history.jsonl: %s", exc)
        return ""


def append_relevant_memory(
    workspace_root: Path,
    insight: str,
    relevance: float = 0.7,
    source_task_id: str = "session",
) -> bool:
    """Append an insight to memory.md if it passes the relevance gate.

    Args:
        workspace_root: Root of the workspace.
        insight: The insight text to persist.
        relevance: Relevance score (0.0-1.0).
        source_task_id: ID of the task that produced this insight.

    Returns True if the insight was saved, False if it was below threshold.
    """
    if relevance < RELEVANCE_THRESHOLD:
        logger.debug(
            "Insight below threshold (%.2f < %.2f), skipping: %s",
            relevance, RELEVANCE_THRESHOLD, insight[:80],
        )
        return False

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    entry = f"- [{now}] (relevance={relevance:.2f}, source={source_task_id}) {insight}"
    append_memory(workspace_root, "memory", entry)
    logger.info("Saved insight to memory.md: %s", insight[:80])
    return True


def run_dream_task(
    workspace_root: Path,
    llm_client=None,
    source_task_id: str = "session",
) -> str:
    """Run the dream consolidation task.

    If an LLM client is available, uses it to extract insights from
    the session log (including recent history.jsonl entries). Otherwise,
    does a simple heuristic extraction of phase outcomes.

    Args:
        workspace_root: Root of the workspace.
        llm_client: Optional LLM client for intelligent extraction.
        source_task_id: Task ID to attribute saved memories to.

    Returns:
        A task-ID-like string ``d-dream-{timestamp}`` conforming to
        the dream task type prefix convention.
    """
    dream_task_id = f"d-dream-{int(time.time())}"

    session_text = read_memory(workspace_root, "session")
    if not session_text or len(session_text.strip()) < 50:
        return dream_task_id

    # Read recent history.jsonl entries for additional context
    history_tail = _read_history_tail(workspace_root)

    if llm_client is not None:
        _llm_dream(workspace_root, session_text, llm_client, source_task_id, history_tail)
    else:
        _heuristic_dream(workspace_root, session_text, source_task_id)

    return dream_task_id


def _llm_dream(
    workspace_root: Path,
    session_text: str,
    llm_client,
    source_task_id: str = "session",
    history_tail: str = "",
) -> list[str]:
    """Use LLM to extract durable insights from the session."""
    history_section = ""
    if history_tail:
        history_section = (
            "\n\nRecent history.jsonl entries:\n" + history_tail
        )

    prompt = (
        "Review the following session log from a PPT generation job. "
        "Extract 1-3 durable insights that would be useful for future jobs. "
        "Each insight should be a single sentence. Focus on:\n"
        "- Patterns in the input materials that affected output quality\n"
        "- Design decisions that worked well or poorly\n"
        "- Warnings or issues that should be remembered\n\n"
        "Return a JSON object with a single key 'insights' containing "
        "a list of objects, each with 'text' (string) and 'relevance' (0.0-1.0)."
    )

    try:
        result = llm_client.generate_json(
            prompt=prompt,
            context={"session_log": session_text[:5000] + history_section},
            phase="dream",
            fallback={"insights": []},
        )
    except Exception as e:
        logger.warning("Dream LLM call failed: %s", e)
        return []

    saved = []
    for item in result.get("insights", []):
        text = item.get("text", "")
        rel = item.get("relevance", 0.7)
        if text and append_relevant_memory(workspace_root, text, rel, source_task_id):
            saved.append(text)
    return saved


def _heuristic_dream(
    workspace_root: Path,
    session_text: str,
    source_task_id: str = "session",
) -> list[str]:
    """Simple heuristic: extract lines that contain 'Warning' or 'Error'."""
    saved = []
    for line in session_text.splitlines():
        stripped = line.strip()
        if any(keyword in stripped.lower() for keyword in ["warning", "error", "failed", "fallback"]):
            if len(stripped) > 20:
                if append_relevant_memory(workspace_root, stripped, 0.65, source_task_id):
                    saved.append(stripped)
    return saved
