"""Tests for the rewritten ToolRegistry — ToolEntry, CATEGORY_PERMISSIONS,
three-layer RBAC, and backward compatibility."""

from __future__ import annotations

import pytest

from ppt_agent.runtime.agent_loop import ToolCall, ToolResult
from ppt_agent.tools.registry import (
    ALL_ROLES,
    CATEGORY_PERMISSIONS,
    COORDINATOR_ONLY,
    FULL_ACCESS,
    ROLE_COORDINATOR,
    ROLE_MAIN_AGENT,
    ROLE_WORKER,
    WORKER_AND_ABOVE,
    ToolDescriptor,
    ToolEntry,
    ToolRegistry,
    create_default_registry,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _echo_executor(call_id: str, args: dict) -> ToolResult:
    return ToolResult(call_id=call_id, output={"received": args}, success=True)


# ── ToolEntry proxy ──────────────────────────────────────────────────

def test_tool_entry_proxies_all_descriptor_attributes() -> None:
    """ToolEntry now proxies name, category, roles, description, parameters, allows_role, to_llm_dict."""
    desc = ToolDescriptor(
        name="test_tool",
        category="artifact",
        roles=FULL_ACCESS,
        description="A test tool.",
        parameters={"x": {"type": "integer"}},
    )
    entry = ToolEntry(descriptor=desc, executor=_echo_executor)

    assert entry.name == "test_tool"
    assert entry.category == "artifact"
    assert entry.roles == FULL_ACCESS
    assert entry.description == "A test tool."
    assert entry.parameters == {"x": {"type": "integer"}}
    assert entry.allows_role(ROLE_COORDINATOR) is True
    assert entry.allows_role("unknown_role") is False
    assert entry.to_llm_dict() == {"name": "test_tool", "description": "A test tool.", "parameters": {"x": {"type": "integer"}}}


def test_tool_entry_is_frozen() -> None:
    """ToolEntry is frozen=True — cannot reassign fields."""
    desc = ToolDescriptor("t", "artifact", FULL_ACCESS)
    entry = ToolEntry(descriptor=desc, executor=_echo_executor)
    with pytest.raises(Exception):  # FrozenInstanceError or similar
        entry._descriptor = desc  # type: ignore


# ── Registration ─────────────────────────────────────────────────────

def test_register_tool_binds_descriptor_and_executor() -> None:
    registry = ToolRegistry()
    desc = ToolDescriptor("my_tool", "retrieval", WORKER_AND_ABOVE)
    registry.register_tool(desc, _echo_executor)

    assert registry.tool_count == 1
    entry = registry.get("my_tool")
    assert isinstance(entry, ToolEntry)
    assert entry.executor is _echo_executor


def test_register_tool_simple() -> None:
    registry = ToolRegistry()
    registry.register_tool_simple(
        name="simple",
        category="filesystem",
        roles=WORKER_AND_ABOVE,
        executor=_echo_executor,
        description="simple tool",
    )
    entry = registry.get("simple")
    assert entry.name == "simple"
    assert entry.category == "filesystem"
    assert entry.executor is _echo_executor


def test_duplicate_registration_raises() -> None:
    registry = ToolRegistry()
    desc = ToolDescriptor("dup", "artifact", FULL_ACCESS)
    registry.register_tool(desc, _echo_executor)
    with pytest.raises(ValueError, match="already registered"):
        registry.register_tool(desc, _echo_executor)


def test_backward_compatible_register_descriptor_only() -> None:
    """Existing register(descriptor) still works — but executor returns error."""
    registry = ToolRegistry()
    desc = ToolDescriptor("desc_only", "assembly", WORKER_AND_ABOVE)
    registry.register(desc)
    assert registry.tool_count == 1

    result = registry.execute(ToolCall(tool_name="desc_only", arguments={}), ROLE_WORKER)
    assert not result.success
    assert "descriptor-only" in (result.error or "")


def test_registration_with_unknown_category_warns() -> None:
    """Registering a tool with an unknown category emits a warning."""
    registry = ToolRegistry()
    desc = ToolDescriptor("bad_cat", "nonexistent_category", FULL_ACCESS)
    with pytest.warns(UserWarning, match="nonexistent_category"):
        registry.register(desc)


def test_cross_method_duplicate() -> None:
    """register() then register_tool() with same name raises ValueError."""
    registry = ToolRegistry()
    registry.register(ToolDescriptor("same", "artifact", FULL_ACCESS))
    with pytest.raises(ValueError, match="already registered"):
        registry.register_tool(ToolDescriptor("same", "artifact", FULL_ACCESS), _echo_executor)


# ── Three-layer RBAC ─────────────────────────────────────────────────

def test_layer1_unregistered_tool_raises_keyerror() -> None:
    registry = ToolRegistry()
    with pytest.raises(KeyError, match="not registered"):
        registry.check_permission("nonexistent", ROLE_WORKER)


def test_layer2_category_blocked_for_coordinator() -> None:
    """Coordinator cannot use retrieval-category tools."""
    registry = ToolRegistry()
    desc = ToolDescriptor("search_kb", "retrieval", WORKER_AND_ABOVE)
    registry.register_tool(desc, _echo_executor)
    with pytest.raises(PermissionError, match="retrieval"):
        registry.check_permission("search_kb", ROLE_COORDINATOR)


def test_layer2_orchestration_blocked_for_worker() -> None:
    """Worker cannot use orchestration-category tools."""
    registry = ToolRegistry()
    desc = ToolDescriptor("spawn_agent", "orchestration", COORDINATOR_ONLY)
    registry.register_tool(desc, _echo_executor)
    with pytest.raises(PermissionError, match="orchestration"):
        registry.check_permission("spawn_agent", ROLE_WORKER)


def test_layer3_tool_level_role_check() -> None:
    """Even if category allows, tool-level roles must match."""
    registry = ToolRegistry()
    # artifact category → FULL_ACCESS at the category level,
    # but the tool only allows coordinator
    desc = ToolDescriptor("coord_only_artifact", "artifact", COORDINATOR_ONLY)
    registry.register_tool(desc, _echo_executor)

    # category check passes (FULL_ACCESS), but tool-level fails
    with pytest.raises(PermissionError, match="cannot use tool"):
        registry.check_permission("coord_only_artifact", ROLE_WORKER)

    # coordinator passes both layers
    registry.check_permission("coord_only_artifact", ROLE_COORDINATOR)  # no raise


def test_execute_permission_failure_returns_error() -> None:
    """execute() returns failed ToolResult (does not raise) on permission error."""
    registry = ToolRegistry()
    desc = ToolDescriptor("orchestrate", "orchestration", COORDINATOR_ONLY)
    registry.register_tool(desc, _echo_executor)

    result = registry.execute(
        ToolCall(tool_name="orchestrate", arguments={}), ROLE_WORKER,
    )
    assert not result.success


def test_execute_success() -> None:
    registry = ToolRegistry()
    registry.register_tool_simple(
        "add", "artifact", FULL_ACCESS, _echo_executor, "adds numbers",
    )
    result = registry.execute(
        ToolCall(tool_name="add", call_id="tc-1", arguments={"a": 1, "b": 2}), ROLE_COORDINATOR,
    )
    assert result.success
    assert result.output["received"] == {"a": 1, "b": 2}
    assert result.call_id == "tc-1"  # call_id preserved


def test_execute_unregistered_tool_returns_error() -> None:
    """execute() with unregistered tool returns failed ToolResult."""
    registry = ToolRegistry()
    result = registry.execute(ToolCall(tool_name="nope", arguments={}), ROLE_WORKER)
    assert not result.success


def test_execute_preserves_call_id_on_both_paths() -> None:
    """call_id is always set correctly regardless of success/failure."""
    registry = ToolRegistry()
    registry.register_tool_simple("ok", "artifact", FULL_ACCESS, _echo_executor)

    # Success path
    result = registry.execute(ToolCall(tool_name="ok", call_id="my-call-1", arguments={}), ROLE_WORKER)
    assert result.call_id == "my-call-1"

    # Failure path (unregistered)
    result2 = registry.execute(ToolCall(tool_name="nope", call_id="my-call-2", arguments={}), ROLE_WORKER)
    assert result2.call_id == "my-call-2"


# ── Error messages do not leak allowed roles ────────────────────────

def test_permission_error_does_not_leak_allowed_roles() -> None:
    """PermissionError messages should not enumerate allowed roles (security)."""
    registry = ToolRegistry()
    desc = ToolDescriptor("secret", "orchestration", COORDINATOR_ONLY)
    registry.register_tool(desc, _echo_executor)

    with pytest.raises(PermissionError) as exc_info:
        registry.check_permission("secret", ROLE_WORKER)
    msg = str(exc_info.value)
    # Should mention the category but not enumerate the allowed role set
    assert "orchestration" in msg
    assert "COORDINATOR_ONLY" not in msg
    assert str(COORDINATOR_ONLY) not in msg


# ── Role-based filtering ────────────────────────────────────────────

def test_get_tools_for_role_worker_excludes_orchestration() -> None:
    registry = create_default_registry()
    tools = registry.get_tools_for_role(ROLE_WORKER)
    names = {t.name for t in tools}
    assert "AgentTool" not in names
    assert "TaskStopTool" not in names
    assert "SyntheticOutput" not in names
    assert "FileSystemTool" in names


def test_get_tools_for_role_coordinator_only_gets_orchestration_and_communication() -> None:
    registry = create_default_registry()
    tools = registry.get_tools_for_role(ROLE_COORDINATOR)
    names = {t.name for t in tools}
    assert "AgentTool" in names
    assert "SyntheticOutput" in names
    assert "SendMessageTool" in names
    # Coordinator does NOT get worker-only tools
    assert "JsonArtifactTool" not in names
    assert "RetrievalTool" not in names


def test_list_tools_with_role_uses_layer2_category_gating() -> None:
    """list_tools(role=...) now delegates to get_tools_for_role (Layer 2 + 3)."""
    registry = create_default_registry()
    # main_agent is in WORKER_AND_ABOVE, so allows_role returns True for
    # orchestration tools. But Layer 2 blocks main_agent from orchestration.
    tools = registry.list_tools(ROLE_MAIN_AGENT)
    names = {t.name for t in tools}
    assert "AgentTool" not in names, "main_agent should not see orchestration tools (Layer 2)"
    assert "FileSystemTool" in names


def test_list_tools_without_role_returns_all() -> None:
    registry = create_default_registry()
    all_tools = registry.list_tools()
    assert len(all_tools) == registry.tool_count


def test_list_tools_with_role_consistent_with_get_tools_for_role() -> None:
    """list_tools(role) and get_tools_for_role(role) return identical results."""
    registry = create_default_registry()
    for role in [ROLE_WORKER, ROLE_COORDINATOR, ROLE_MAIN_AGENT]:
        assert set(t.name for t in registry.list_tools(role)) == set(
            t.name for t in registry.get_tools_for_role(role)
        )


def test_get_tools_for_role_as_dicts_returns_llm_ready() -> None:
    registry = create_default_registry()
    dicts = registry.get_tools_for_role_as_dicts(ROLE_WORKER)
    assert len(dicts) > 0
    for d in dicts:
        assert "name" in d
        assert "description" in d
        assert "parameters" in d


def test_get_descriptor() -> None:
    registry = create_default_registry()
    desc = registry.get_descriptor("AgentTool")
    assert isinstance(desc, ToolDescriptor)
    assert desc.name == "AgentTool"
    with pytest.raises(KeyError):
        registry.get_descriptor("nonexistent")


def test_to_llm_dict_format() -> None:
    desc = ToolDescriptor(
        name="fmt", category="artifact", roles=FULL_ACCESS,
        description="desc", parameters={"x": {"type": "int"}},
    )
    d = desc.to_llm_dict()
    assert d == {"name": "fmt", "description": "desc", "parameters": {"x": {"type": "int"}}}


# ── Category listing ────────────────────────────────────────────────

def test_list_by_category() -> None:
    registry = create_default_registry()
    orchestration = registry.list_by_category("orchestration")
    assert len(orchestration) == 4
    names = {e.name for e in orchestration}
    assert names == {"AgentTool", "TaskStopTool", "SendMessageTool", "SyntheticOutput"}


# ── CATEGORY_PERMISSIONS completeness ────────────────────────────────

def test_category_permissions_covers_all_known_categories() -> None:
    """Every category in the default registry has an entry in CATEGORY_PERMISSIONS."""
    registry = create_default_registry()
    for entry in registry._entries.values():
        assert entry.category in CATEGORY_PERMISSIONS, (
            f"Category '{entry.category}' missing from CATEGORY_PERMISSIONS"
        )


# ── Empty registry edge cases ────────────────────────────────────────

def test_empty_registry_operations() -> None:
    registry = ToolRegistry()
    assert registry.list_tools() == []
    assert registry.get_tools_for_role(ROLE_WORKER) == []
    assert registry.get_tools_for_role_as_dicts(ROLE_WORKER) == []
    assert registry.list_by_category("artifact") == []
    assert registry.tool_count == 0
