from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")


@dataclass
class ToolExecutionContext:
    job_id: str
    tool_name: str
    timeout_seconds: int = 60
    max_attempts: int = 1


def execute_tool(context: ToolExecutionContext, fn: Callable[[], T]) -> T:
    last_error: Exception | None = None
    for _ in range(context.max_attempts):
        try:
            return fn()
        except Exception as exc:  # pragma: no cover - intentionally simple retry boundary
            last_error = exc
    assert last_error is not None
    raise last_error
