"""JSONL-based conversation persistence.

Implements AGENT_ARCHITECTURE.md §2.2 / §2.4:
  - Append-only JSONL files for crash-consistent persistence
  - Node-based conversation tree (node_id / parent_id) for optional branching
  - Full ContentBlock fidelity — all block types are preserved on round-trip
  - Lossless archiving — full history always preserved on disk

Thread safety: a ``threading.RLock`` serialises both reads and writes.
On Windows this prevents partial-line reads when a concurrent write is
mid-flight.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ppt_agent.llm.messages import ContentBlock, LLMMessage

# Valid LLMMessage roles — used to validate deserialized entries.
_VALID_ROLES = frozenset({"system", "user", "assistant", "tool"})


@dataclass
class ConversationEntry:
    """A single persisted conversation entry.

    Mirrors the wire format described in AGENT_ARCHITECTURE.md §2.2.
    """

    id: str                         # unique message id, e.g. "msg_<uuid_hex[:12]>"
    role: str                       # "system" | "user" | "assistant" | "tool"
    content: str                    # text extracted from all text-typed ContentBlocks
    timestamp: float                # time.time()
    node_id: str                    # conversation-tree node id (auto-generated when omitted)
    parent_id: str | None = None    # parent node id, for branching
    tool_calls: list[dict] | None = None  # tool calls on assistant messages
    agent_id: str = ""              # producing agent id
    metadata: dict = field(default_factory=dict)
    # Full ContentBlock list serialized as JSON — preserves type, path,
    # mime_type, and image_data across round-trips.
    content_blocks_json: str | None = None


class ConversationStore:
    """JSONL-based conversation persistence (AGENT_ARCHITECTURE.md §2.2).

    Each message is stored as one JSON line in ``history.jsonl``.
    Append-only writes avoid corruption on partial failures.

    Usage::

        store = ConversationStore(Path("workspace/jobs/demo/.conversation"))
        entry = store.append(msg, node_id="n1")
        messages = store.load_messages(limit=50)

    Thread-safety: a reentrant lock serialises all file I/O.  Reads and
    writes are mutually exclusive, preventing partial-line reads on
    Windows.
    """

    def __init__(self, conversation_dir: Path) -> None:
        self.conversation_dir = Path(conversation_dir)
        self.conversation_dir.mkdir(parents=True, exist_ok=True)
        self.history_path = self.conversation_dir / "history.jsonl"
        self._rwlock = threading.RLock()

    # ── Write ──────────────────────────────────────────────────────

    def append(
        self,
        message: LLMMessage,
        *,
        node_id: str | None = None,
        parent_id: str | None = None,
        tool_calls: list[dict] | None = None,
        agent_id: str = "",
        metadata: dict | None = None,
    ) -> ConversationEntry:
        """Append a message to the JSONL history.

        Args:
            message: The LLMMessage to persist.  All ContentBlocks
                (including images, json, tool_result) are serialised.
            node_id: Conversation-tree node id.  Auto-generated when omitted.
            parent_id: Parent node id (optional, for branching).
            tool_calls: Tool-call list on assistant messages.
            agent_id: Producing agent identifier.
            metadata: Arbitrary extra key-value pairs.

        Returns:
            The created ConversationEntry.
        """
        if node_id is None:
            node_id = f"msg_{uuid.uuid4().hex[:12]}"

        # Serialize full ContentBlock list for lossless round-trip
        blocks_json = json.dumps(
            [_content_block_to_dict(b) for b in message.content],
            ensure_ascii=False,
        )

        # Extract text for the summary/content field
        text_parts: list[str] = []
        for block in message.content:
            if block.text:
                text_parts.append(block.text)
        content = "\n".join(text_parts)

        entry = ConversationEntry(
            id=f"msg_{uuid.uuid4().hex[:12]}",
            role=message.role,
            content=content,
            timestamp=time.time(),
            node_id=node_id,
            parent_id=parent_id,
            tool_calls=tool_calls,
            agent_id=agent_id,
            metadata=metadata or {},
            content_blocks_json=blocks_json,
        )

        line = self._serialize_entry(entry)
        with self._rwlock:
            try:
                with self.history_path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                logger.exception("Failed to append to conversation history")
                raise

        return entry

    # ── Read ───────────────────────────────────────────────────────

    def load_messages(self, limit: int | None = None) -> list[LLMMessage]:
        """Load messages from history, optionally limited to the last *N*.

        Returns LLMMessage objects with full ContentBlock fidelity.
        Metadata fields (node_id, agent_id, …) are not included in the
        returned messages.

        Args:
            limit: Return only the last *N* messages.  ``None`` = all.

        Returns:
            LLMMessage list in chronological order.
        """
        entries = self.load_entries(limit=limit)
        return [self._entry_to_message(e) for e in entries]

    def load_entries(self, limit: int | None = None) -> list[ConversationEntry]:
        """Load raw ConversationEntry objects (includes full metadata).

        Uses a streaming, bounded-memory approach: the file is read
        line-by-line and only the last *limit* entries are retained.
        This prevents OOM on large history files.

        Useful for compression decisions, statistics, or branching logic
        that needs access to ``node_id``, ``agent_id``, ``tool_calls``, etc.
        """
        entries: list[ConversationEntry] = []
        if limit is not None and limit > 0:
            # Bounded window — use a deque, never hold more than limit+1
            window: deque[ConversationEntry] = deque(maxlen=limit)
            for entry in self._iter_entries():
                window.append(entry)
            entries = list(window)
        else:
            entries = list(self._iter_entries())
        return entries

    def _iter_entries(self):
        """Generator: yield one ConversationEntry at a time from the file.

        Corrupt or unparseable lines are silently skipped.
        """
        try:
            with self._rwlock:
                try:
                    with self.history_path.open("r", encoding="utf-8") as fh:
                        for line in fh:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                yield self._deserialize_entry(line)
                            except (json.JSONDecodeError, KeyError):
                                # Corrupt line — skip
                                continue
                except FileNotFoundError:
                    return
        except OSError:
            logger.exception("Failed to read conversation history")
            return

    def get_stats(self) -> dict[str, Any]:
        """Return conversation statistics.

        Returns a dict with keys:
            message_count, total_chars, oldest_timestamp,
            newest_timestamp, roles (count per role).
        """
        count = 0
        total_chars = 0
        oldest: float | None = None
        newest: float | None = None
        roles: dict[str, int] = {}

        for entry in self._iter_entries():
            count += 1
            total_chars += len(entry.content)
            roles[entry.role] = roles.get(entry.role, 0) + 1
            ts = entry.timestamp
            if oldest is None or ts < oldest:
                oldest = ts
            if newest is None or ts > newest:
                newest = ts

        return {
            "message_count": count,
            "total_chars": total_chars,
            "oldest_timestamp": oldest,
            "newest_timestamp": newest,
            "roles": roles,
        }

    # ── Internal ───────────────────────────────────────────────────

    @staticmethod
    def _serialize_entry(entry: ConversationEntry) -> str:
        data: dict[str, Any] = {
            "id": entry.id,
            "role": entry.role,
            "content": entry.content,
            "timestamp": entry.timestamp,
            "node_id": entry.node_id,
        }
        if entry.parent_id is not None:
            data["parent_id"] = entry.parent_id
        if entry.tool_calls is not None:
            data["tool_calls"] = entry.tool_calls
        if entry.agent_id:
            data["agent_id"] = entry.agent_id
        if entry.metadata:
            data["metadata"] = entry.metadata
        if entry.content_blocks_json is not None:
            data["content_blocks"] = entry.content_blocks_json
        return json.dumps(data, ensure_ascii=False)

    @staticmethod
    def _deserialize_entry(line: str) -> ConversationEntry:
        data = json.loads(line)
        # Validate role to catch corrupt data
        role = data["role"]
        if role not in _VALID_ROLES:
            raise KeyError(f"Invalid role: {role!r}")

        return ConversationEntry(
            id=data["id"],
            role=role,
            content=data["content"],
            timestamp=data["timestamp"],
            node_id=data["node_id"],
            parent_id=data.get("parent_id"),
            tool_calls=data.get("tool_calls"),
            agent_id=data.get("agent_id", ""),
            metadata=data.get("metadata", {}),
            content_blocks_json=data.get("content_blocks"),
        )

    @staticmethod
    def _entry_to_message(entry: ConversationEntry) -> LLMMessage:
        """Convert a ConversationEntry back to an LLMMessage.

        If ``content_blocks_json`` is present, reconstruct the full
        ContentBlock list (preserving types, paths, mime_types).
        Otherwise fall back to a single text block from ``entry.content``.
        """
        if entry.content_blocks_json:
            try:
                raw_blocks = json.loads(entry.content_blocks_json)
                blocks = [_dict_to_content_block(d) for d in raw_blocks]
                return LLMMessage(role=entry.role, content=blocks)  # type: ignore[arg-type]
            except (json.JSONDecodeError, KeyError, TypeError):
                pass  # fall through to text-only fallback

        return LLMMessage(
            role=entry.role,  # type: ignore[arg-type]
            content=[ContentBlock.text_block(entry.content)],
        )


# ── ContentBlock serialization helpers ─────────────────────────────────

def _content_block_to_dict(block: ContentBlock) -> dict[str, Any]:
    """Serialize a ContentBlock to a plain dict (lossless)."""
    d: dict[str, Any] = {"type": block.type}
    if block.text is not None:
        d["text"] = block.text
    if block.path is not None:
        d["path"] = block.path
    if block.mime_type is not None:
        d["mime_type"] = block.mime_type
    if block.data is not None:
        d["data"] = block.data
    if block.image_data is not None:
        d["image_data"] = block.image_data
    return d


def _dict_to_content_block(d: dict[str, Any]) -> ContentBlock:
    """Deserialize a ContentBlock from a dict."""
    return ContentBlock(
        type=d.get("type", "text"),
        text=d.get("text"),
        path=d.get("path"),
        mime_type=d.get("mime_type"),
        data=d.get("data"),
        image_data=d.get("image_data"),
    )


import logging
logger = logging.getLogger(__name__)
