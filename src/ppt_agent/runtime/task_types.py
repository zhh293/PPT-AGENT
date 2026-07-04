"""Seven task types as defined in AGENT_ARCHITECTURE.md §9.1.

Each task type has distinct execution semantics, ID prefix,
and isolation guarantees.
"""

from __future__ import annotations

from enum import Enum


class TaskType(str, Enum):
    """The seven task types from the architecture spec."""
    LOCAL_BASH = "local_bash"                  # b- prefix: background shell
    LOCAL_AGENT = "local_agent"                # a- prefix: sync/async sub-agent
    REMOTE_AGENT = "remote_agent"              # r- prefix: remote session
    IN_PROCESS_TEAMMATE = "in_process_teammate"  # t- prefix: persistent teammate
    LOCAL_WORKFLOW = "local_workflow"           # w- prefix: multi-step workflow
    MONITOR_MCP = "monitor_mcp"                # m- prefix: passive monitoring
    DREAM = "dream"                            # d- prefix: memory consolidation


# Canonical ID prefix for each task type (§9.1)
TASK_PREFIXES = {
    TaskType.LOCAL_BASH: "b",
    TaskType.LOCAL_AGENT: "a",
    TaskType.REMOTE_AGENT: "r",
    TaskType.IN_PROCESS_TEAMMATE: "t",
    TaskType.LOCAL_WORKFLOW: "w",
    TaskType.MONITOR_MCP: "m",
    TaskType.DREAM: "d",
}

# Legacy mapping for backward compatibility with old tests
_LEGACY_PREFIXES = {
    "coordinator": "coord",
    "worker": "work",
    "tool": "tool",
}


def make_task_id(kind: str | TaskType, serial: int) -> str:
    """Generate a task ID with the appropriate prefix.

    Supports both the new TaskType enum and legacy string kinds.

    Examples:
        make_task_id(TaskType.LOCAL_AGENT, 1)  → "a-0001"
        make_task_id("worker", 1)              → "work-0001"  (legacy)
    """
    if isinstance(kind, TaskType):
        prefix = TASK_PREFIXES[kind]
        return f"{prefix}-{serial:04d}"

    # Legacy support
    if kind in _LEGACY_PREFIXES:
        return f"{_LEGACY_PREFIXES[kind]}-{serial:04d}"

    # Try matching by string value
    try:
        task_type = TaskType(kind)
        prefix = TASK_PREFIXES[task_type]
        return f"{prefix}-{serial:04d}"
    except ValueError:
        raise ValueError(f"Unknown task kind: {kind}")


class TaskStatus(str, Enum):
    """Lifecycle states for a managed task."""
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"      # Waiting for sub-task or user input
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Valid transitions
_VALID_TRANSITIONS = {
    TaskStatus.PENDING: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {TaskStatus.WAITING, TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.WAITING: {TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.FAILED},
    TaskStatus.COMPLETED: set(),   # Terminal
    TaskStatus.FAILED: set(),      # Terminal
    TaskStatus.CANCELLED: set(),   # Terminal
}


def validate_transition(current: TaskStatus, target: TaskStatus) -> bool:
    """Check if a state transition is valid."""
    return target in _VALID_TRANSITIONS.get(current, set())
