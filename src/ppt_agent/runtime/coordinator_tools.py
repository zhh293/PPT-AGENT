from __future__ import annotations

from dataclasses import dataclass


ALLOWED_COORDINATOR_TOOLS = {"AgentTool", "TaskStopTool", "SendMessageTool", "SyntheticOutput"}


@dataclass
class AgentTool:
    name: str = "AgentTool"


@dataclass
class TaskStopTool:
    name: str = "TaskStopTool"


@dataclass
class SendMessageTool:
    name: str = "SendMessageTool"


@dataclass
class SyntheticOutput:
    payload: dict


def assert_allowed_tool(tool_name: str) -> None:
    if tool_name not in ALLOWED_COORDINATOR_TOOLS:
        raise PermissionError(f"Coordinator tool not allowed: {tool_name}")
