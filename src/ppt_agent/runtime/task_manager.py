from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ManagedTask:
    task_id: str
    status: str = "pending"
    parent_id: str | None = None
    children: list[str] = field(default_factory=list)


class TaskManager:
    def __init__(self) -> None:
        self.tasks: dict[str, ManagedTask] = {}

    def create(self, task_id: str, parent_id: str | None = None) -> ManagedTask:
        task = ManagedTask(task_id=task_id, parent_id=parent_id)
        self.tasks[task_id] = task
        if parent_id and parent_id in self.tasks:
            self.tasks[parent_id].children.append(task_id)
        return task

    def transition(self, task_id: str, status: str) -> None:
        self.tasks[task_id].status = status
