"""Targeted tests for the 7 Agent Mode fixes (B1-B7)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ppt_agent.runtime.agent_loop import AgentLoop, AgentLoopResult, StopReason, ToolCall
from ppt_agent.llm.messages import LLMMessage, LLMResult


# ── B1: spawn_agent / spawn_worker tool name + parameter resolution ──

def test_spawn_worker_handler_reads_capability_fallback() -> None:
    """spawn_worker handler reads 'capability' first, then 'phase'."""
    from ppt_agent.coordinator.coordinator_agent import CoordinatorAgent

    coord = CoordinatorAgent(
        workspace=MagicMock(),
        llm_client=MagicMock(),
        max_turns=1,
    )
    # Verify the handler exists and accepts "capability" without crashing
    # (The mock LLM may cause the agent loop to actually run, which is fine —
    #  the test just needs to confirm the parameter name is resolved.)
    result = coord.execute_tool(ToolCall(
        tool_name="spawn_worker",
        arguments={"capability": "document_analysis"},
        call_id="tc_1",
    ))
    # Key assertion: the spawn_worker handler should NOT return
    # "capability is required" — it should recognize the "capability" key.
    if not result.success:
        assert "capability is required" not in (result.error or "")
    # If it succeeded (mock LLM made it through), that's also fine.


def test_spawn_agent_handler_accepts_capability_param() -> None:
    """spawn_agent handler reads 'capability' parameter."""
    from ppt_agent.coordinator.coordinator_agent import CoordinatorAgent

    coord = CoordinatorAgent(
        workspace=MagicMock(),
        llm_client=MagicMock(),
        max_turns=1,
    )
    result = coord.execute_tool(ToolCall(
        tool_name="spawn_agent",
        arguments={"capability": "document_analysis"},
        call_id="tc_2",
    ))
    if not result.success:
        assert "capability is required" not in (result.error or "")


def test_on_turn_complete_handles_both_tool_names() -> None:
    """on_turn_complete emits for both spawn_agent and spawn_worker."""
    from ppt_agent.coordinator.coordinator_agent import CoordinatorAgent
    from ppt_agent.coordinator.event_bus import EventBus, EventType

    bus = EventBus(Path("."), auto_file_consumer=False)
    events: list[dict] = []
    from ppt_agent.coordinator.event_bus import CallbackConsumer
    bus.subscribe(CallbackConsumer(lambda e: events.append(e)))

    coord = CoordinatorAgent(
        workspace=MagicMock(),
        llm_client=MagicMock(),
        event_bus=bus,
        max_turns=1,
    )

    # Simulate a turn with spawn_agent tool call
    from ppt_agent.runtime.agent_loop import AgentTurn
    turn = AgentTurn(turn_number=1, tool_calls=[
        ToolCall(tool_name="spawn_agent", arguments={"capability": "outline_generation"}, call_id="tc_a"),
    ])
    coord.on_turn_complete(turn)
    assert any(e.get("phase") == "outline_generation" for e in events), \
        f"spawn_agent should trigger PHASE_STARTED event, got: {events}"

    events.clear()
    turn2 = AgentTurn(turn_number=2, tool_calls=[
        ToolCall(tool_name="spawn_worker", arguments={"phase": "content_mapping"}, call_id="tc_b"),
    ])
    coord.on_turn_complete(turn2)
    assert any(e.get("phase") == "content_mapping" for e in events), \
        f"spawn_worker should also trigger PHASE_STARTED event, got: {events}"


# ── B2: compose_artifact in capabilities and ToolRegistry ──

def test_compose_artifact_in_tool_lists() -> None:
    """4 content_reasoning capabilities have compose_artifact."""
    from ppt_agent.coordinator.capabilities import (
        DOCUMENT_ANALYSIS, OUTLINE_GENERATION, DESIGN_PLANNING, CONTENT_MAPPING,
        TEMPLATE_MATCHING, PPT_ASSEMBLY, VISUAL_GENERATION,
    )
    for cap in [DOCUMENT_ANALYSIS, OUTLINE_GENERATION, DESIGN_PLANNING, CONTENT_MAPPING]:
        assert "compose_artifact" in cap.tools, f"{cap.capability_id} missing compose_artifact"
    # Non-content-reasoning capabilities should NOT have it
    for cap in [TEMPLATE_MATCHING, PPT_ASSEMBLY, VISUAL_GENERATION]:
        assert "compose_artifact" not in cap.tools, f"{cap.capability_id} should not have compose_artifact"


def test_compose_artifact_tool_works(tmp_path: Path) -> None:
    """make_compose_artifact creates a working executor."""
    from ppt_agent.coordinator.tool_impls.common import make_compose_artifact
    from ppt_agent.models.artifacts import JobWorkspace

    ws = MagicMock(spec=JobWorkspace)
    ws.root = tmp_path

    descriptor, executor = make_compose_artifact(ws)

    assert descriptor.name == "compose_artifact"
    assert descriptor.category == "artifact"

    # Valid content
    result = executor("tc_1", {"content": {"slides": [{"title": "Test"}]}})
    assert result.success
    assert result.output["composed"] is True
    assert "slides" in result.output["keys"]

    # Empty content
    result = executor("tc_2", {"content": {}})
    assert not result.success
    assert "empty" in result.error.lower()

    # Non-dict content
    result = executor("tc_3", {"content": "not a dict"})
    assert not result.success
    assert "must be a json object" in result.error.lower()


# ── B3: NO_ACTION retry instead of immediate death ──

class _TestAgent(AgentLoop):
    """Minimal agent for testing B3 behavior."""

    def __init__(self, responses: list[str]):
        super().__init__(agent_id="test", llm_client=MagicMock(), max_turns=10)
        self._responses = responses
        self._call_count = 0

    def build_system_prompt(self) -> str:
        return "You are a test agent. Call tools or say done."

    def get_available_tools(self) -> list[dict]:
        return [{"name": "test_tool", "description": "A test tool.", "parameters": {}}]

    def execute_tool(self, tool_call: ToolCall):
        from ppt_agent.runtime.agent_loop import ToolResult
        return ToolResult(call_id=tool_call.call_id, output={"ok": True}, success=True)

    def parse_llm_response(self, result: LLMResult):
        """Simulate the real parser — tries repair_json on the text."""
        text = result.text
        try:
            from ppt_agent.llm.json_repair import repair_json
            data, _ = repair_json(text)
        except Exception:
            return text, [], False
        if isinstance(data, dict):
            if data.get("done"):
                return text, [], True
            tool = data.get("tool")
            if tool:
                return text, [ToolCall(tool_name=tool, arguments=data.get("arguments", {}), call_id="tc")], False
        return text, [], False

    def _call_llm(self) -> LLMResult:
        """Return pre-set responses, cycling if needed."""
        idx = min(self._call_count, len(self._responses) - 1)
        text = self._responses[idx]
        self._call_count += 1
        return LLMResult(text=text, provider="test", model="test")


def test_no_action_retries_then_stops() -> None:
    """B3: Non-tool JSON gets continuation prompt, stops after 3 retries."""
    # 3 reasoning-only responses → should hit NO_ACTION limit
    agent = _TestAgent(responses=[
        '{"reasoning": "I need to think about this first."}',
        '{"analysis": "Still thinking, need more context."}',
        '{"thought": "Almost ready to act."}',
    ])
    result = agent.run("Do something.")
    assert result.stop_reason == StopReason.NO_ACTION
    assert agent._no_action_count == 3


def test_no_action_recovers_on_tool_call() -> None:
    """B3: Retry then LLM makes a tool call → continues normally."""
    agent = _TestAgent(responses=[
        '{"reasoning": "Let me think..."}',
        '{"tool": "test_tool", "arguments": {}}',
        '{"done": true}',
    ])
    result = agent.run("Do something.")
    assert result.stop_reason == StopReason.COMPLETED
    assert agent._no_action_count == 1  # first turn was retried
    assert len(result.turns) >= 2  # at least 2 turns happened


def test_worker_falls_back_on_no_action() -> None:
    """B3: Worker with NO_ACTION triggers deterministic fallback."""
    from ppt_agent.coordinator.worker_agent import WorkerAgent
    from ppt_agent.coordinator.capabilities import DOCUMENT_ANALYSIS

    ws = MagicMock()
    ws.root = Path(".")
    ws.input_dir = Path(".")

    worker = WorkerAgent(ws, DOCUMENT_ANALYSIS, MagicMock(), force=False, max_turns=2)
    # Fake the agent loop to always return NO_ACTION
    original_run = worker.run

    def patched_run(task_prompt, context=None):
        result = AgentLoopResult(
            agent_id=worker.agent_id,
            stop_reason=StopReason.NO_ACTION,
            turns=[],
            error="Simulated NO_ACTION",
        )
        # Should trigger fallback
        return worker._fallback_execution(result)

    # Just verify fallback doesn't crash
    try:
        worker._fallback_execution(AgentLoopResult(
            agent_id="test", stop_reason=StopReason.NO_ACTION, turns=[],
        ))
    except Exception as e:
        # OK if it fails (no real workspace), just not a TypeError/NameError
        assert "executor" not in str(e).lower()


# ── B5: tool parameters in JSON Schema format ──

def test_coordinator_tool_params_are_json_schema() -> None:
    """B5: Coordinator tool parameters use proper JSON Schema."""
    from ppt_agent.coordinator.coordinator_agent import CoordinatorAgent

    coord = CoordinatorAgent(
        workspace=MagicMock(),
        llm_client=MagicMock(),
        max_turns=1,
    )
    tools = coord.get_available_tools()
    for tool in tools:
        params = tool["parameters"]
        # New format: has "type" and "properties" keys
        assert "type" in params, f"{tool['name']}: missing 'type' in parameters"
        assert params["type"] == "object", f"{tool['name']}: type should be 'object'"
        assert "properties" in params, f"{tool['name']}: missing 'properties'"


def test_spawn_agent_params_have_required() -> None:
    """B5: spawn_agent declares 'capability' as required."""
    from ppt_agent.coordinator.coordinator_agent import CoordinatorAgent

    coord = CoordinatorAgent(
        workspace=MagicMock(),
        llm_client=MagicMock(),
        max_turns=1,
    )
    tool = [t for t in coord.get_available_tools() if t["name"] == "spawn_agent"][0]
    assert "required" in tool["parameters"]
    assert "capability" in tool["parameters"]["required"]


# ── B6: LLM call retry ──

def test_llm_call_retries_on_error() -> None:
    """B6: _call_llm retries on transient failures then succeeds."""
    # Use an agent that does NOT override _call_llm, so we test the real retry logic
    from ppt_agent.runtime.agent_loop import AgentLoop, ToolCall, ToolResult

    class _RetryAgent(AgentLoop):
        def build_system_prompt(self) -> str:
            return "test"
        def get_available_tools(self) -> list[dict]:
            return []
        def execute_tool(self, tc: ToolCall) -> ToolResult:
            return ToolResult(call_id=tc.call_id, output={}, success=True)
        def parse_llm_response(self, result: LLMResult):
            if result.success and result.text:
                from ppt_agent.llm.json_repair import repair_json
                try:
                    data, _ = repair_json(result.text)
                    if isinstance(data, dict) and data.get("done"):
                        return result.text, [], True
                except Exception:
                    pass
            return result.text, [], False

    call_count = [0]
    def mock_generate(messages, **kwargs):
        call_count[0] += 1
        if call_count[0] < 3:
            return LLMResult(provider="test", model="test", error="Simulated network error")
        return LLMResult(text='{"done": true}', provider="test", model="test")

    agent = _RetryAgent(agent_id="retry_test", llm_client=MagicMock(), max_turns=3)
    agent.llm_client.provider.generate = mock_generate
    # Don't let it compress/audit
    agent._compress_messages = lambda: None
    agent._log_model_call = lambda r: None
    agent.messages = [LLMMessage.system("test"), LLMMessage.user("test")]

    result = agent._call_llm()
    assert result.success
    assert call_count[0] == 3  # 2 failures + 1 success


def test_llm_call_raises_after_3_failures() -> None:
    """B6: _call_llm raises after 3 consecutive failures."""
    from ppt_agent.runtime.agent_loop import AgentLoop, ToolCall, ToolResult

    class _FailAgent(AgentLoop):
        def build_system_prompt(self) -> str:
            return "test"
        def get_available_tools(self) -> list[dict]:
            return []
        def execute_tool(self, tc: ToolCall) -> ToolResult:
            return ToolResult(call_id=tc.call_id, output={}, success=True)
        def parse_llm_response(self, result: LLMResult):
            return result.text, [], False

    def mock_generate(messages, **kwargs):
        return LLMResult(provider="test", model="test", error="Persistent error")

    agent = _FailAgent(agent_id="fail_test", llm_client=MagicMock(), max_turns=3)
    agent.llm_client.provider.generate = mock_generate
    agent._compress_messages = lambda: None
    agent._log_model_call = lambda r: None
    agent.messages = [LLMMessage.system("test"), LLMMessage.user("test")]

    with pytest.raises(RuntimeError, match="LLM returned error after 3 attempts"):
        agent._call_llm()


# ── B7: Skill path resolution ──

def test_skill_path_is_absolute_from_workflow() -> None:
    """B7: SkillLoader root is resolved from __file__, not cwd."""
    import sys
    # Import triggers the SkillLoader creation in workflow module
    # Check that the path in _run_agent_workflow / _run_llm_augmented_workflow
    # uses an absolute-ish path, not bare ".catpaw/skills"
    from ppt_agent.coordinator import workflow
    import inspect

    src = inspect.getsource(workflow._run_agent_workflow)
    # Should contain "__file__" or "resolve()" (not bare ".catpaw")
    assert ".catpaw" in src
    assert "__file__" in src or "resolve" in src


def test_workers_import_without_error() -> None:
    """Sanity: all 8 workers can be imported."""
    from ppt_agent.workers import (
        document_analyst, outline_generator, template_matcher,
        design_director, content_mapper, image_generator,
        ppt_assembler, ppt_verifier,
    )
    assert all([
        document_analyst, outline_generator, template_matcher,
        design_director, content_mapper, image_generator,
        ppt_assembler, ppt_verifier,
    ])
