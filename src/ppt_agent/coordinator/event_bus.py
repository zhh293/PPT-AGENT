"""Event bus with publish/subscribe and pluggable consumers.

Events are the primary communication mechanism between phases.
All events are persisted to history.jsonl and dispatched to
registered consumers.

Event types (from AGENT_ARCHITECTURE.md Section 6):
- phase_started / phase_completed
- artifact_written
- tool_call_start / tool_call_result
- llm_call_start / llm_call_result
- warning / error
- progress
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

logger = logging.getLogger(__name__)


# ─── Event Types ────────────────────────────────────────────────────────
class EventType:
    PHASE_STARTED = "phase_started"
    PHASE_COMPLETED = "phase_completed"
    ARTIFACT_WRITTEN = "artifact_written"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_RESULT = "tool_call_result"
    LLM_CALL_START = "llm_call_start"
    LLM_CALL_RESULT = "llm_call_result"
    WARNING = "warning"
    ERROR = "error"
    PROGRESS = "progress"
    USER_INPUT_REQUEST = "user_input_request"

    # ── Additional event types (AGENT_ARCHITECTURE.md) ──
    TEXT_DELTA = "text_delta"                    # streaming text from LLM
    AGENT_SPAWN = "agent_spawn"                  # when a worker agent is created
    AGENT_COMPLETE = "agent_complete"            # when a worker agent finishes
    STATUS_CHANGE = "status_change"              # task status transitions
    MEMORY_UPDATE = "memory_update"              # memory.md writes
    COMPRESSION_EVENT = "compression_event"      # context compression triggered
    SKILL_LOADED = "skill_loaded"                # progressive skill loading

    TOOL_CALL_CHUNK = "tool_call_chunk"        # streaming tool output

    # Aliases for backward compatibility
    STARTED = "started"
    COMPLETED = "completed"


# ─── Consumer Base ──────────────────────────────────────────────────────
class EventConsumer(ABC):
    """Base class for event consumers."""

    @abstractmethod
    def handle(self, event: dict) -> None:
        """Process an event. Must not raise — errors are logged."""
        ...


class FileConsumer(EventConsumer):
    """Default consumer that writes to history.jsonl."""

    def __init__(self, job_root: Path) -> None:
        self.job_root = job_root

    def handle(self, event: dict) -> None:
        history = self.job_root / "history.jsonl"
        history.parent.mkdir(parents=True, exist_ok=True)
        with history.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")


class LoggingConsumer(EventConsumer):
    """Consumer that logs events to Python logging."""

    def handle(self, event: dict) -> None:
        event_type = event.get("type", "unknown")
        phase = event.get("phase", "")
        message = event.get("message", "")
        level = logging.WARNING if event_type in ("error", "warning") else logging.INFO
        logger.log(level, "[%s] %s: %s", phase, event_type, message)


class CallbackConsumer(EventConsumer):
    """Consumer that calls a user-provided function."""

    def __init__(self, callback: Callable[[dict], None]) -> None:
        self._callback = callback

    def handle(self, event: dict) -> None:
        self._callback(event)


# ─── Event Bus ──────────────────────────────────────────────────────────
class EventBus:
    """Pub/sub event bus with pluggable consumers.

    Usage:
        bus = EventBus(job_root)
        bus.subscribe(LoggingConsumer())
        bus.subscribe(CallbackConsumer(my_handler))
        bus.emit("phase_started", phase="document_analysis", message="Starting")
    """

    def __init__(self, job_root: Path, auto_file_consumer: bool = True) -> None:
        self.job_root = job_root
        # Each entry: (consumer, event_type_filter | None)
        # None means "receive all events"
        self._subscriptions: list[tuple[EventConsumer, set[str] | None]] = []
        self._max_buffer: int = 1000  # backpressure limit per consumer

        # Per-consumer backpressure tracking keyed by id(consumer)
        self._buffer_counts: dict[int, int] = {}
        self._dropped_counts: dict[int, int] = {}

        if auto_file_consumer:
            fc = FileConsumer(job_root)
            self._subscriptions.append((fc, None))
            self._buffer_counts[id(fc)] = 0
            self._dropped_counts[id(fc)] = 0

    def subscribe(
        self,
        consumer: EventConsumer,
        event_types: list[str] | None = None,
    ) -> None:
        """Register a consumer to receive events.

        Args:
            consumer: The event consumer.
            event_types: Optional list of event type strings to subscribe to.
                When provided, the consumer only receives events whose type
                matches one of these strings. When ``None`` (the default) or
                ``["*"]``, the consumer receives **all** events.
        """
        type_filter: set[str] | None = None
        if event_types is not None and event_types != ["*"]:
            type_filter = set(event_types)
        self._subscriptions.append((consumer, type_filter))
        self._buffer_counts[id(consumer)] = 0
        self._dropped_counts[id(consumer)] = 0

    def unsubscribe(self, consumer: EventConsumer) -> None:
        """Remove a consumer."""
        self._subscriptions = [
            (c, f) for c, f in self._subscriptions if c is not consumer
        ]
        self._buffer_counts.pop(id(consumer), None)
        self._dropped_counts.pop(id(consumer), None)

    def emit(
        self,
        event_type: str,
        *,
        phase: str = "",
        message: str = "",
        artifact_path: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Emit an event to all consumers.

        Returns the constructed event dict.
        """
        event = {
            "event_id": f"evt_{uuid4().hex[:12]}",
            "job_id": self.job_root.name,
            "phase": phase,
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": message,
        }
        if artifact_path:
            event["artifact_path"] = artifact_path
        if metadata:
            event["metadata"] = metadata

        self._dispatch(event)
        return event

    # ── Internal dispatch ────────────────────────────────────────────────

    def _dispatch(self, event: dict) -> None:
        """Dispatch an event to matching consumers with per-consumer backpressure."""
        event_type = event.get("type", "")

        for consumer, type_filter in self._subscriptions:
            # Per-event-type filtering
            if type_filter is not None and event_type not in type_filter:
                continue

            cid = id(consumer)
            count = self._buffer_counts.get(cid, 0)

            # Per-consumer backpressure check
            if count >= self._max_buffer:
                self._dropped_counts[cid] = self._dropped_counts.get(cid, 0) + 1
                logger.warning(
                    "Event bus backpressure: consumer %s has dropped %d events.",
                    type(consumer).__name__,
                    self._dropped_counts[cid],
                )
                continue

            try:
                consumer.handle(event)
                self._buffer_counts[cid] = count + 1
            except Exception as e:
                logger.error("Consumer %s failed: %s", type(consumer).__name__, e)

    @property
    def stats(self) -> dict:
        total = sum(self._buffer_counts.values())
        dropped = sum(self._dropped_counts.values())
        return {
            "total_events": total,
            "dropped_events": dropped,
            "consumer_count": len(self._subscriptions),
        }


# ─── Legacy function (backward compatibility) ──────────────────────────
def emit_event(
    job_root: Path,
    phase: str,
    event_type: str,
    message: str = "",
    artifact_path: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Emit a single event (backward-compatible function).

    Creates a one-shot EventBus with FileConsumer and emits.
    For high-throughput scenarios, use EventBus directly.
    """
    event = {
        "event_id": f"evt_{uuid4().hex[:12]}",
        "job_id": Path(job_root).name,
        "phase": phase,
        "type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": message,
    }
    if artifact_path:
        event["artifact_path"] = artifact_path
    if metadata:
        event["metadata"] = metadata
    history = Path(job_root) / "history.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    with history.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event
