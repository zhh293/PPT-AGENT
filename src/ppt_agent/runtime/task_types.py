from __future__ import annotations

TASK_PREFIXES = {
    "coordinator": "coord",
    "worker": "work",
    "tool": "tool",
}


def make_task_id(kind: str, serial: int) -> str:
    if kind not in TASK_PREFIXES:
        raise ValueError(f"Unknown task kind: {kind}")
    return f"{TASK_PREFIXES[kind]}-{serial:04d}"
