"""Tests for Phase 6 — capabilities, ToolFactory, tool_impls, and orchestration upgrades."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ppt_agent.coordinator.capabilities import (
    CAPABILITY_REGISTRY, CAPABILITIES_IN_ORDER,
    AgentCapability, get_capability,
    DOCUMENT_ANALYSIS, OUTLINE_GENERATION, CONTENT_MAPPING, PPT_ASSEMBLY, QUALITY_VERIFICATION,
)
from ppt_agent.coordinator.tool_factory import ToolFactory
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.tools.registry import ROLE_WORKER, ToolEntry


# ── Capability registry ──────────────────────────────────────────────

def test_registry_has_8_capabilities() -> None:
    assert len(CAPABILITY_REGISTRY) == 8

def test_get_capability_returns_correct_one() -> None:
    cap = get_capability("document_analysis")
    assert cap.capability_id == "document_analysis"
    assert cap.input_artifacts == []
    assert "source_summary" in cap.output_artifacts

def test_get_capability_raises_on_unknown() -> None:
    with pytest.raises(KeyError):
        get_capability("nonexistent")

def test_content_mapping_has_5_inputs() -> None:
    cap = get_capability("content_mapping")
    assert len(cap.input_artifacts) == 5

def test_all_capabilities_have_fallback_module() -> None:
    for cap in CAPABILITIES_IN_ORDER:
        assert cap.fallback_module, f"{cap.capability_id} missing fallback_module"

def test_all_capabilities_have_tools() -> None:
    for cap in CAPABILITIES_IN_ORDER:
        assert len(cap.tools) >= 3, f"{cap.capability_id} needs at least 3 tools"
        assert "write_artifact" in cap.tools
        assert "validate_output" in cap.tools

def test_capabilities_are_in_pipeline_order() -> None:
    ids = [c.capability_id for c in CAPABILITIES_IN_ORDER]
    assert ids[0] == "document_analysis"
    assert ids[-1] == "quality_verification"


# ── ToolFactory ──────────────────────────────────────────────────────

def test_tool_factory_creates_registry(tmp_path: Path) -> None:
    workspace = MagicMock(spec=JobWorkspace)
    workspace.root = tmp_path
    workspace.input_dir = tmp_path / "input"
    workspace.input_dir.mkdir(parents=True, exist_ok=True)
    workspace.artifact_path = lambda name: tmp_path / f"{name}.json"

    factory = ToolFactory(workspace)
    registry = factory.create_registry_for_capability(DOCUMENT_ANALYSIS)

    # Should have common tools + domain tools
    assert registry.tool_count >= 3
    assert registry.get("read_input_files") is not None
    assert registry.get("read_artifact") is not None

def test_tool_factory_no_domain_module_graceful(tmp_path: Path) -> None:
    """Capabilities without domain tools still get common tools."""
    workspace = MagicMock(spec=JobWorkspace)
    workspace.root = tmp_path
    workspace.artifact_path = lambda name: tmp_path / f"{name}.json"
    factory = ToolFactory(workspace)
    registry = factory.create_registry_for_capability(QUALITY_VERIFICATION)
    assert registry.tool_count >= 3  # common tools only

def test_factory_registry_enforces_rbac(tmp_path: Path) -> None:
    workspace = MagicMock(spec=JobWorkspace)
    workspace.root = tmp_path
    workspace.artifact_path = lambda name: tmp_path / f"{name}.json"
    factory = ToolFactory(workspace)
    registry = factory.create_registry_for_capability(PPT_ASSEMBLY)

    tools = registry.get_tools_for_role(ROLE_WORKER)
    names = {t.name for t in tools}
    assert "assemble_pptx" in names
    assert "read_artifact" in names


# ── Workers created with capability ──────────────────────────────────

def test_worker_accepts_agent_capability(tmp_path: Path) -> None:
    from ppt_agent.coordinator.worker_agent import WorkerAgent
    workspace = MagicMock(spec=JobWorkspace)
    workspace.root = tmp_path
    workspace.input_dir = tmp_path / "input"
    workspace.input_dir.mkdir(parents=True, exist_ok=True)
    workspace.artifact_path = lambda name: tmp_path / f"{name}.json"

    worker = WorkerAgent(workspace, DOCUMENT_ANALYSIS, MagicMock(), max_turns=2)
    assert worker.phase == "document_analysis"
    assert worker.capability.capability_id == "document_analysis"

def test_worker_accepts_string_phase_backward_compat(tmp_path: Path) -> None:
    from ppt_agent.coordinator.worker_agent import WorkerAgent
    workspace = MagicMock(spec=JobWorkspace)
    workspace.root = tmp_path
    workspace.input_dir = tmp_path / "input"
    workspace.input_dir.mkdir(parents=True, exist_ok=True)
    workspace.artifact_path = lambda name: tmp_path / f"{name}.json"

    worker = WorkerAgent(workspace, "outline_generation", MagicMock(), max_turns=2)
    assert worker.phase == "outline_generation"

def test_worker_uses_registry_tools(tmp_path: Path) -> None:
    from ppt_agent.coordinator.worker_agent import WorkerAgent
    from ppt_agent.tools.registry import ToolDescriptor, FULL_ACCESS, ToolRegistry

    registry = ToolRegistry()
    registry.register_tool_simple("test_tool", "artifact", FULL_ACCESS,
                                   lambda cid, args: MagicMock(success=True), "test")

    workspace = MagicMock(spec=JobWorkspace)
    workspace.root = tmp_path
    workspace.input_dir = tmp_path / "input"
    workspace.input_dir.mkdir(parents=True, exist_ok=True)
    workspace.artifact_path = lambda name: tmp_path / f"{name}.json"

    worker = WorkerAgent(workspace, DOCUMENT_ANALYSIS, MagicMock(), max_turns=2, registry=registry)
    tools = worker.get_available_tools()
    assert any(t["name"] == "test_tool" for t in tools)
