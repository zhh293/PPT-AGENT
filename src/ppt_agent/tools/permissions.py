from __future__ import annotations

from ppt_agent.tools.registry import ToolDescriptor


def assert_tool_allowed(descriptor: ToolDescriptor, role: str, category: str | None = None) -> None:
    if role not in descriptor.roles:
        raise PermissionError(f"{role} cannot use {descriptor.name}")
    if category and descriptor.category != category:
        raise PermissionError(f"{descriptor.name} is not in category {category}")
