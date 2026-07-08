"""Tests for Phase 4.2-4.4 — SSEConsumer, sandbox shell security, event completeness."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ppt_agent.coordinator.event_bus import (
    EventBus,
    SSEConsumer,
    EventType,
)
from ppt_agent.tools.filesystem import SafeFilesystem


# ── SSEConsumer ─────────────────────────────────────────────────────

def test_sse_consumer_queues_events() -> None:
    consumer = SSEConsumer(max_queue_size=10)
    consumer.handle({"type": "test", "data": "hello"})
    # Queue should have 1 item
    assert not consumer._queue.empty()


def test_sse_consumer_drops_when_full() -> None:
    consumer = SSEConsumer(max_queue_size=2)
    consumer.handle({"type": "a"})
    consumer.handle({"type": "b"})
    consumer.handle({"type": "c"})  # queue full — silently dropped
    assert consumer._queue.full()


def test_sse_consumer_event_generator() -> None:
    consumer = SSEConsumer(max_queue_size=5)
    consumer.handle({"type": "t1"})
    consumer.handle({"type": "t2"})

    async def _collect():
        items = []
        async for evt in consumer.event_generator():
            items.append(evt)
            if len(items) >= 2:
                break
        return items

    items = asyncio.run(_collect())
    assert len(items) == 2
    assert items[0]["type"] == "t1"


def test_sse_consumer_subscribed_to_bus(tmp_path: Path) -> None:
    bus = EventBus(tmp_path)
    before = len(bus._subscriptions)  # EventBus auto-creates FileConsumer
    consumer = SSEConsumer()
    bus.subscribe(consumer)
    assert len(bus._subscriptions) == before + 1


# ── SafeFilesystem ──────────────────────────────────────────────────

def test_safe_fs_resolve_inside_root(tmp_path: Path) -> None:
    fs = SafeFilesystem(tmp_path)
    resolved = fs.resolve("subdir/file.txt")
    assert resolved == (tmp_path / "subdir" / "file.txt").resolve()


def test_safe_fs_resolve_blocks_escape(tmp_path: Path) -> None:
    fs = SafeFilesystem(tmp_path)
    with pytest.raises(PermissionError, match="escapes sandbox"):
        fs.resolve("../../etc/passwd")


def test_safe_fs_atomic_write(tmp_path: Path) -> None:
    fs = SafeFilesystem(tmp_path)
    path = fs.atomic_write_text("data/output.txt", "content")
    assert path.exists()
    assert path.read_text() == "content"


# ── Event type completeness ─────────────────────────────────────────

def test_all_required_event_types_defined() -> None:
    """Verify that the 16 event types from the architecture spec exist."""
    required = {
        "phase_started", "phase_completed", "artifact_written",
        "tool_call_start", "tool_call_result", "tool_call_chunk",
        "llm_call_start", "llm_call_result",
        "text_delta", "warning", "error", "progress",
        "agent_spawn", "agent_complete",
        "compression_event", "skill_loaded",
        "status_change", "user_input_request",
    }
    defined = {getattr(EventType, attr) for attr in dir(EventType) if not attr.startswith("_")}
    missing = required - defined
    assert not missing, f"Missing event types: {missing}"
