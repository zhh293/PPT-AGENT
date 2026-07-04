"""Permission enforcement for tool access.

Provides RBAC checking against the tool registry.
"""

from __future__ import annotations

from ppt_agent.tools.registry import ToolDescriptor, ToolRegistry


def assert_tool_allowed(
    descriptor: ToolDescriptor,
    role: str,
    category: str | None = None,
    allowlist: set[str] | None = None,
) -> None:
    """Raise PermissionError if a role cannot use a tool.

    Three-step check (AGENT_ARCHITECTURE.md §5.3):
      1. Is tool registered? (handled by caller via registry.get)
      2. Does caller role have category access?
      3. Is tool in caller's explicit allowlist?

    Args:
        descriptor: The tool descriptor to check.
        role: The caller's role.
        category: Optional required category.
        allowlist: Optional explicit set of tool names the caller is
            permitted to use.  When provided and non-None, the tool
            name must appear in this set.
    """
    if role not in descriptor.roles:
        raise PermissionError(f"Role '{role}' cannot use tool '{descriptor.name}'")
    if category and descriptor.category != category:
        raise PermissionError(f"Tool '{descriptor.name}' is not in category '{category}'")
    if allowlist is not None and descriptor.name not in allowlist:
        raise PermissionError(
            f"Tool '{descriptor.name}' is not in caller's allowlist"
        )


def check_phase_permissions(
    registry: ToolRegistry,
    tool_names: list[str],
    role: str,
    allowlist: set[str] | None = None,
) -> list[str]:
    """Check which tools a role can access from a list.

    Returns list of denied tool names.  When *allowlist* is provided,
    tools not in the allowlist are also treated as denied.
    """
    denied = []
    for name in tool_names:
        try:
            descriptor = registry.get(name)
            assert_tool_allowed(descriptor, role, allowlist=allowlist)
        except (PermissionError, KeyError):
            denied.append(name)
    return denied
