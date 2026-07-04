"""Context compression with 5 graduated levels.

L0: Full context (no compression)
L1: Remove verbose formatting, normalize whitespace
L2: Summarize evidence items, truncate long lists
L3: Keep only approved text and current-phase data
L4: Emergency — only errors, approved text, and active tool results

Compression invariants (NEVER removed at any level):
- agent.md content
- memory.md content
- session.md content
- Error records
- Active tool call results
- Most recent 2 messages

When an ``llm_client`` is provided to :func:`compress_context`, L2+ uses
LLM self-summarization for long text fields instead of heuristic
truncation.  If the LLM call fails, the function falls back to the
existing truncation behaviour.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# Fields that are never compressed at any level
INVARIANT_KEYS = {
    "approved_text",
    "errors",
    "agent_identity",
    "memory",
    "session",
    "active_tool_results",
    "recent_messages",
}

# Strings longer than this (in characters) are candidates for summarization
_LONG_STRING_THRESHOLD = 2000


def compress_context(
    context: dict,
    level: int | str = 0,
    llm_client=None,
) -> dict:
    """Apply graduated compression to a context dict.

    Args:
        context: The context dictionary to compress.
        level: Compression level 0-4 (int or string like "L4").
        llm_client: Optional :class:`~ppt_agent.llm.client.LLMClient`.  When
            provided and *level* >= 2, long text fields are summarized via
            the LLM instead of being heuristically truncated.  If the LLM
            call fails, truncation is used as a fallback.

    Returns:
        A new dict with compression applied.
    """
    level = _parse_level(level) if not isinstance(level, int) else level
    if level <= 0:
        return context.copy()

    result = {}

    # Always preserve invariants
    for key in INVARIANT_KEYS:
        if key in context:
            result[key] = context[key]

    if level >= 1:
        # L1: Include everything but normalize strings
        for key, value in context.items():
            if key in result:
                continue
            if isinstance(value, str):
                result[key] = _normalize_whitespace(value)
            elif isinstance(value, list):
                result[key] = _truncate_list(value, max_items=20)
            else:
                result[key] = value

    if level >= 2:
        # L2: Summarize long lists, compress long strings
        for key, value in result.items():
            if key in INVARIANT_KEYS:
                continue
            if isinstance(value, list) and len(value) > 8:
                result[key] = _truncate_list(value, max_items=8)
            if isinstance(value, str) and len(value) > _LONG_STRING_THRESHOLD:
                result[key] = _compress_long_string(value, llm_client)

    if level >= 3:
        # L3: Keep only phase-relevant data
        keep_keys = INVARIANT_KEYS | {"slide_contents", "outline", "source_summary", "phase", "job_id"}
        result = {k: v for k, v in result.items() if k in keep_keys}

    if level >= 4:
        # L4: Emergency — only errors, approved text, active results
        result = {k: v for k, v in result.items() if k in INVARIANT_KEYS}

    return result


def _compress_long_string(text: str, llm_client=None) -> str:
    """Compress a long string, preferring LLM summarization when available.

    Falls back to heuristic truncation if no LLM client is provided or
    the LLM call fails.
    """
    if llm_client is not None:
        summarized = _llm_summarize(llm_client, text, max_tokens=500)
        if summarized:
            return summarized
    # Heuristic fallback
    return text[:_LONG_STRING_THRESHOLD] + "... [truncated]"


def _llm_summarize(llm_client, text: str, max_tokens: int = 500) -> str:
    """Use LLM to create a concise summary of the text.

    Calls ``llm_client.provider.generate()`` directly (bypassing
    :meth:`~ppt_agent.llm.client.LLMClient.generate_text` to avoid
    re-entrant context compression) with a summarization prompt.

    Returns an empty string on failure so the caller can fall back to
    truncation.
    """
    # Lazy import to avoid circular dependency between compression and llm
    try:
        from ppt_agent.llm.messages import LLMMessage
    except ImportError:
        logger.debug("LLMMessage not available; cannot use LLM summarization")
        return ""

    summarization_prompt = (
        f"Summarize the following text concisely in under {max_tokens} tokens. "
        "Preserve all key facts, decisions, and actionable information.\n\n"
        f"{text}"
    )

    messages = [
        LLMMessage.system(
            "You are a summarization assistant. Provide a concise, "
            "faithful summary that preserves all important information."
        ),
        LLMMessage.user(summarization_prompt),
    ]

    try:
        result = llm_client.provider.generate(
            messages,
            max_tokens=max_tokens,
            temperature=0.0,
        )
        if result.success and result.text:
            return result.text.strip()
        logger.warning(
            "LLM summarization failed: %s; falling back to truncation",
            result.error,
        )
        return ""
    except Exception as exc:
        logger.warning(
            "LLM summarization raised %s: %s; falling back to truncation",
            type(exc).__name__,
            exc,
        )
        return ""


def _normalize_whitespace(text: str) -> str:
    """Collapse multiple whitespace into single spaces."""
    import re
    return re.sub(r"\s+", " ", text).strip()


def _truncate_list(items: list, max_items: int = 10) -> list:
    """Truncate a list and add a marker."""
    if len(items) <= max_items:
        return items
    return items[:max_items] + [f"... ({len(items) - max_items} more items)"]


def _parse_level(level) -> int:
    """Parse level from int or string like 'L4'."""
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        return int(level.replace("L", "").replace("l", ""))
    return 0


def assert_preserves_approved_text(original: dict, compressed: dict) -> None:
    """Assert that approved_text is preserved through compression."""
    orig_text = original.get("approved_text")
    comp_text = compressed.get("approved_text")
    if orig_text is not None and comp_text != orig_text:
        raise AssertionError(
            f"Compression lost approved_text: {orig_text} -> {comp_text}"
        )
