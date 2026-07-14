"""File-based mailbox for inter-agent communication.

Implements the file mailbox protocol from AGENT_ARCHITECTURE.md §10.1:
    ~/.agent/teams/{team_name}/inboxes/{agent_name}.json

Features:
    - File locking for concurrency safety (§10.1.2)
    - Message priority ordering (§9.4.2)
    - Unread message filtering
    - JSONL append-only format
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import IntEnum

# fcntl is Unix-only; on Windows we fall back to no locking.
# Concurrent writes to the same mailbox file in Agent Mode (ThreadPoolExecutor)
# may produce interleaved lines on Windows.  This is a known limitation — the
# append-only JSONL format tolerates occasional corruption (a line is either
# fully written or not), but cross-agent message ordering is best-effort.
try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False
from pathlib import Path

logger = logging.getLogger(__name__)


class MessagePriority(IntEnum):
    """Message priority levels (§9.4.2). Lower value = higher priority."""
    SHUTDOWN = 1       # Highest: prevents zombie agents
    TEAM_LEAD = 2      # Leader represents user intent
    PEER = 3           # FIFO among equals
    BACKGROUND = 4     # Background work / unclaimed tasks


class Mailbox:
    """Thread-safe, file-based mailbox with locking and priority ordering.

    Each agent has its own mailbox file. Messages are appended atomically
    with file locking to prevent corruption under concurrent access.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    @contextmanager
    def _file_lock(self, mode: str = "r+"):
        """Acquire an exclusive file lock for atomic read-modify-write.

        On Unix this uses fcntl.flock.  On Windows locking is skipped
        (best-effort; the append-only write pattern is safe for small
        concurrent writes in most cases).
        """
        if not self.path.exists():
            self.path.touch()

        fh = self.path.open(mode, encoding="utf-8")
        try:
            if _HAS_FCNTL:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            yield fh
        finally:
            if _HAS_FCNTL:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()

    def append(
        self,
        sender: str,
        recipient: str,
        message: str,
        artifact_refs: list[str] | None = None,
        priority: MessagePriority | int = MessagePriority.PEER,
    ) -> dict:
        """Append a message to the mailbox with file locking.

        Args:
            sender: Sending agent ID.
            recipient: Target agent ID.
            message: Message content.
            artifact_refs: Optional list of artifact paths referenced.
            priority: Message priority (default: PEER).

        Returns:
            The message dict that was written.
        """
        item = {
            "sender": sender,
            "recipient": recipient,
            "message": message,
            "artifact_refs": artifact_refs or [],
            "priority": int(priority),
            "read": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        with self._file_lock("a") as fh:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")

        logger.debug("Mailbox %s: message from %s (priority=%d)", self.path.name, sender, priority)
        return item

    def read_all(self) -> list[dict]:
        """Read all messages, sorted by priority then timestamp."""
        messages = self._load_messages()
        return sorted(messages, key=lambda m: (m.get("priority", 3), m.get("timestamp", "")))

    def read_unread(self) -> list[dict]:
        """Read only unread messages, sorted by priority then timestamp."""
        messages = self._load_messages()
        unread = [m for m in messages if not m.get("read", False)]
        return sorted(unread, key=lambda m: (m.get("priority", 3), m.get("timestamp", "")))

    def mark_read(self, sender: str | None = None) -> int:
        """Mark messages as read. If sender is given, only mark from that sender.

        Returns the number of messages marked.
        """
        with self._file_lock("r+") as fh:
            content = fh.read()
            if not content.strip():
                return 0

            lines = content.strip().splitlines()
            messages = []
            count = 0
            for line in lines:
                if not line.strip():
                    continue
                msg = json.loads(line)
                if not msg.get("read", False):
                    if sender is None or msg.get("sender") == sender:
                        msg["read"] = True
                        count += 1
                messages.append(msg)

            # Rewrite file with updated read status
            fh.seek(0)
            fh.truncate()
            for msg in messages:
                fh.write(json.dumps(msg, ensure_ascii=False) + "\n")

            return count

    def has_shutdown_request(self) -> bool:
        """Check if there's a pending shutdown message (priority 1)."""
        messages = self.read_unread()
        return any(m.get("priority", 3) == MessagePriority.SHUTDOWN for m in messages)

    def pending_count(self) -> int:
        """Count unread messages."""
        return len(self.read_unread())

    def _load_messages(self) -> list[dict]:
        """Load all messages from the JSONL file."""
        try:
            content = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []

        messages = []
        for line in content.splitlines():
            line = line.strip()
            if line:
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("Mailbox %s: skipping corrupt line", self.path.name)
        return messages
