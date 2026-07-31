from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from unittest.mock import MagicMock

from ppt_agent.coordinator.coordinator_agent import CoordinatorAgent
from ppt_agent.models.artifacts import JobWorkspace, atomic_write_json
from ppt_agent.runtime.agent_loop import ToolResult
from ppt_agent.runtime.agent_loop import AgentLoop, ToolCall
from ppt_agent.llm.messages import LLMResult


def _workspace(tmp_path: Path) -> JobWorkspace:
    workspace = JobWorkspace(tmp_path / "job")
    workspace.ensure()
    atomic_write_json(
        workspace.root / "job.json",
        {"job_id": workspace.root.name, "status": "created"},
    )
    return workspace


def test_coordinator_profile_routes_to_coordinator_workflow(tmp_path, monkeypatch) -> None:
    from ppt_agent.coordinator import workflow

    workspace = _workspace(tmp_path)
    client = MagicMock()
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        workflow,
        "_create_llm_client",
        lambda profile, job_root: observed.update(profile=profile) or client,
    )
    monkeypatch.setattr(
        workflow,
        "_run_agent_workflow",
        lambda ws, llm, bus, phases, force: observed.update(
            workspace=ws, phases=phases, force=force
        )
        or [ws.root / "final.pptx"],
    )

    result = workflow.run_workflow(
        workspace,
        model_profile="coordinator:deepseek",
        force=True,
    )

    assert observed["profile"] == "deepseek"
    assert observed["workspace"] == workspace
    assert observed["phases"] == workflow.PHASES
    assert result == [workspace.root / "final.pptx"]


def test_coordinator_rejects_phase_with_missing_input_artifacts(tmp_path) -> None:
    coordinator = CoordinatorAgent(
        workspace=_workspace(tmp_path),
        llm_client=MagicMock(),
        phases=["outline_generation"],
    )
    try:
        result = coordinator._spawn_agent("outline_generation", "", False)
    finally:
        coordinator.shutdown()

    assert not result.success
    assert "missing required artifacts" in (result.error or "")
    assert "source_summary" in (result.error or "")


def test_coordinator_enforces_phase_scope(tmp_path) -> None:
    coordinator = CoordinatorAgent(
        workspace=_workspace(tmp_path),
        llm_client=MagicMock(),
        phases=["document_analysis"],
    )
    try:
        result = coordinator._spawn_agent("outline_generation", "", False)
    finally:
        coordinator.shutdown()

    assert not result.success
    assert "outside the requested workflow scope" in (result.error or "")


def test_coordinator_blocks_post_mapping_phase_before_review(tmp_path) -> None:
    workspace = _workspace(tmp_path)
    atomic_write_json(
        workspace.artifact_path("slide_contents"),
        {"review_status": "draft", "slides": []},
    )
    atomic_write_json(workspace.artifact_path("template_zones"), {"slides": []})
    coordinator = CoordinatorAgent(
        workspace=workspace,
        llm_client=MagicMock(),
        phases=["visual_generation"],
        force=False,
    )
    try:
        result = coordinator._spawn_agent("visual_generation", "", False)
    finally:
        coordinator.shutdown()

    assert not result.success
    assert "user review is required" in (result.error or "")
    assert (workspace.root / "review_pending.json").exists()


def test_review_result_resolves_worker_result_by_task_id(tmp_path) -> None:
    coordinator = CoordinatorAgent(
        workspace=_workspace(tmp_path),
        llm_client=MagicMock(),
    )
    coordinator._task_capabilities["a-1"] = "outline_generation"
    coordinator._worker_results["outline_generation"] = {
        "status": "completed",
        "output": "outline.json",
    }
    try:
        result = coordinator._review_result("a-1")
    finally:
        coordinator.shutdown()

    assert result.success
    assert result.output["capability"] == "outline_generation"
    assert result.output["status"] == "completed"


def test_wait_agents_propagates_worker_failure(tmp_path) -> None:
    coordinator = CoordinatorAgent(
        workspace=_workspace(tmp_path),
        llm_client=MagicMock(),
    )
    future: Future = Future()
    future.set_result(
        ToolResult(call_id="", output={"status": "failed"}, success=False, error="boom")
    )
    coordinator._futures["a-1"] = future
    try:
        result = coordinator._wait_agents(["a-1"], 1)
    finally:
        coordinator.shutdown()

    assert not result.success
    assert result.output["a-1"]["status"] == "failed"
    assert result.output["a-1"]["error"] == "boom"


def test_agent_loop_observes_unsuccessful_tool_result() -> None:
    class RejectingAgent(AgentLoop):
        def __init__(self) -> None:
            super().__init__(agent_id="rejecting", llm_client=MagicMock(), max_turns=2)
            self.calls = 0

        def build_system_prompt(self) -> str:
            return "Call the guarded tool."

        def get_available_tools(self) -> list[dict]:
            return [{"name": "guarded", "description": "guarded", "parameters": {}}]

        def _call_llm(self) -> LLMResult:
            self.calls += 1
            return LLMResult(text='{"tool":"guarded","arguments":{}}' if self.calls == 1 else '{"done":true}')

        def parse_llm_response(self, result: LLMResult):
            if "done" in result.text:
                return result.text, [], True
            return result.text, [ToolCall("guarded", {}, "tc-1")], False

        def execute_tool(self, tool_call: ToolCall) -> ToolResult:
            return ToolResult(call_id=tool_call.call_id, output=None, success=False, error="blocked")

    agent = RejectingAgent()
    result = agent.run("go")

    assert result.turns[0].tool_results[0].success is False
    assert any("[Tool Error: guarded]" in message.text() for message in agent.messages)
    assert any("blocked" in message.text() for message in agent.messages)


def test_production_coordinator_uses_direct_llm_augmented_worker(tmp_path) -> None:
    workspace = _workspace(tmp_path)
    (workspace.input_dir / "project.md").write_text("# Atlas\nEvidence-backed planning.", encoding="utf-8")
    client = MagicMock()
    client.generate_json.return_value = {
        "project_name": "Atlas",
        "domain": "software/product",
        "target_audience": "business stakeholders",
        "tone": "professional",
        "value_proposition": "Evidence-backed planning.",
        "product_capabilities": ["Planning"],
        "evidence_items": [],
        "unsupported_claims": [],
        "warnings": [],
        "confidence": 0.9,
    }
    coordinator = CoordinatorAgent(
        workspace=workspace,
        llm_client=client,
        phases=["document_analysis"],
        force=True,
    )
    try:
        result = coordinator._spawn_agent("document_analysis", "", False)
    finally:
        coordinator.shutdown()

    assert result.success
    assert workspace.artifact_path("source_summary").exists()
    assert coordinator.worker_results["document_analysis"]["turns"] == 0
    assert (workspace.root / "dispatch_decisions.jsonl").exists()


def test_reconcile_coordinator_state_clears_stale_running_phase(tmp_path) -> None:
    from ppt_agent.coordinator.workflow import _reconcile_coordinator_state

    workspace = _workspace(tmp_path)
    atomic_write_json(workspace.artifact_path("source_summary"), {"project_name": "Atlas"})
    atomic_write_json(
        workspace.root / "workflow_state.json",
        {
            "status": "running",
            "running_phases": ["outline_generation"],
            "phase_states": {
                "document_analysis": {"status": "completed"},
                "outline_generation": {"status": "running"},
            },
        },
    )

    _reconcile_coordinator_state(
        workspace,
        ["document_analysis", "outline_generation"],
        status="incomplete",
    )

    state = __import__("json").loads(
        (workspace.root / "workflow_state.json").read_text(encoding="utf-8")
    )
    assert state["status"] == "incomplete"
    assert state["completed_phases"] == ["document_analysis"]
    assert state["running_phases"] == []
    assert state["phase_states"]["outline_generation"]["status"] == "pending"
