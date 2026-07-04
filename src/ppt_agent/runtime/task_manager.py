"""Task manager with full lifecycle management.

Manages task creation, state transitions, parent-child relationships,
and provides queries by type and status.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ppt_agent.coordinator.event_bus import EventBus, EventType
from ppt_agent.runtime.task_types import (
    TaskStatus,
    TaskType,
    make_task_id,
    validate_transition,
)

logger = logging.getLogger(__name__)


@dataclass
class ManagedTask:
    """A tracked task with full lifecycle metadata."""
    task_id: str
    task_type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    parent_id: str | None = None
    children: list[str] = field(default_factory=list)
    description: str = ""
    created_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None
    completed_at: float | None = None
    result: dict | None = None
    error: str | None = None

    # Legacy compatibility: allow string access to status
    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            self.status = TaskStatus(self.status) if self.status in TaskStatus.__members__.values() else TaskStatus.PENDING


class TaskManager:
    """Central task lifecycle manager.

    Tracks all tasks across the system, enforces valid state transitions,
    and maintains parent-child relationships for hierarchical task trees.
    """

    def __init__(
        self,
        *,
        history_path: Path | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self.tasks: dict[str, ManagedTask] = {}
        self._serial_counters: dict[TaskType, int] = {t: 0 for t in TaskType}
        self._history_path = history_path
        self._event_bus = event_bus

    def create(
        self,
        task_type: TaskType,
        *,
        parent_id: str | None = None,
        description: str = "",
        task_id: str | None = None,
    ) -> ManagedTask:
        """Create and register a new task.

        Args:
            task_type: One of the 7 task types.
            parent_id: Optional parent task ID for hierarchical tracking.
            description: Human-readable description of the task.
            task_id: Optional explicit ID (auto-generated if not provided).

        Returns:
            The created ManagedTask.
        """
        if task_id is None:
            self._serial_counters[task_type] += 1
            task_id = make_task_id(task_type, self._serial_counters[task_type])

        task = ManagedTask(
            task_id=task_id,
            task_type=task_type,
            parent_id=parent_id,
            description=description,
        )
        self.tasks[task_id] = task

        if parent_id and parent_id in self.tasks:
            self.tasks[parent_id].children.append(task_id)

        logger.debug("Created task %s (%s): %s", task_id, task_type.value, description)

        # Emit agent_spawn event for agent-like task types
        if self._event_bus is not None and task_type in (
            TaskType.LOCAL_AGENT,
            TaskType.IN_PROCESS_TEAMMATE,
        ):
            self._event_bus.emit(
                EventType.AGENT_SPAWN,
                message=f"Spawned {task_type.value} task {task_id}",
                metadata={
                    "task_id": task_id,
                    "task_type": task_type.value,
                    "parent_id": parent_id,
                    "description": description,
                },
            )

        return task

    def transition(self, task_id: str, status: TaskStatus | str, **kwargs) -> None:
        """Transition a task to a new status.

        Args:
            task_id: The task to transition.
            status: The target status.
            **kwargs: Additional fields to set (result, error).

        Raises:
            KeyError: If task_id doesn't exist.
            ValueError: If the transition is invalid.
        """
        if task_id not in self.tasks:
            raise KeyError(f"Unknown task: {task_id}")

        task = self.tasks[task_id]

        if isinstance(status, str):
            status = TaskStatus(status)

        if not validate_transition(task.status, status):
            raise ValueError(
                f"Invalid transition for {task_id}: {task.status.value} → {status.value}"
            )

        old_status = task.status
        task.status = status

        if status == TaskStatus.RUNNING and task.started_at is None:
            task.started_at = time.monotonic()
        elif status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            task.completed_at = time.monotonic()

        if "result" in kwargs:
            task.result = kwargs["result"]
        if "error" in kwargs:
            task.error = kwargs["error"]

        logger.debug("Task %s: %s → %s", task_id, old_status.value, status.value)

        # Persist transition to history.jsonl
        if self._history_path is not None:
            entry = {
                "task_id": task_id,
                "from_status": old_status.value,
                "to_status": status.value,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            with self._history_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # Emit status_change event
        if self._event_bus is not None:
            self._event_bus.emit(
                EventType.STATUS_CHANGE,
                message=f"Task {task_id}: {old_status.value} → {status.value}",
                metadata={
                    "task_id": task_id,
                    "from_status": old_status.value,
                    "to_status": status.value,
                },
            )

    def get(self, task_id: str) -> ManagedTask:
        """Get a task by ID."""
        if task_id not in self.tasks:
            raise KeyError(f"Unknown task: {task_id}")
        return self.tasks[task_id]

    def list_by_type(self, task_type: TaskType) -> list[ManagedTask]:
        """List all tasks of a given type."""
        return [t for t in self.tasks.values() if t.task_type == task_type]

    def list_by_status(self, status: TaskStatus) -> list[ManagedTask]:
        """List all tasks with a given status."""
        return [t for t in self.tasks.values() if t.status == status]

    def list_children(self, parent_id: str) -> list[ManagedTask]:
        """List all direct children of a task."""
        parent = self.get(parent_id)
        return [self.tasks[cid] for cid in parent.children if cid in self.tasks]

    def all_completed(self, parent_id: str) -> bool:
        """Check if all children of a task have completed (or failed/cancelled)."""
        children = self.list_children(parent_id)
        if not children:
            return True
        terminal = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
        return all(c.status in terminal for c in children)

    @property
    def active_count(self) -> int:
        """Number of currently running or waiting tasks."""
        return sum(
            1 for t in self.tasks.values()
            if t.status in (TaskStatus.RUNNING, TaskStatus.WAITING)
        )
