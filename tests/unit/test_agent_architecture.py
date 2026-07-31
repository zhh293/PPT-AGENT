"""Tests for the complete agent architecture.

Covers:
    - AgentLoop: think→act→observe cycle
    - TaskManager: 7 task types, lifecycle transitions
    - Mailbox: file locking, priority ordering, unread filtering
    - Context compression wired into LLM client
    - CoordinatorAgent: tool set enforcement
    - WorkerAgent: deterministic fallback
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ppt_agent.runtime.agent_loop import (
    AgentLoop,
    AgentLoopResult,
    AgentState,
    AgentTurn,
    StopReason,
    ToolCall,
    ToolResult,
)
from ppt_agent.runtime.task_manager import ManagedTask, TaskManager
from ppt_agent.runtime.task_types import (
    TaskStatus,
    TaskType,
    make_task_id,
    validate_transition,
)
from ppt_agent.runtime.mailbox import Mailbox, MessagePriority
from ppt_agent.context.compression import compress_context


# ── Helper: A minimal concrete AgentLoop for testing ──

class DummyLLMResult:
    """Mimic LLMResult for testing."""
    def __init__(self, text: str = "", success: bool = True, error: str | None = None):
        self.text = text
        self.success = success
        self.error = error
        self.provider = "test"
        self.model = "test"
        self.latency_ms = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.repaired = False
        self.json_data = None


class DummyProvider:
    """Minimal LLM provider for testing."""
    name = "test"

    def __init__(self, responses: list[str] | None = None):
        self._responses = responses or ['{"done": true, "result": "ok"}']
        self._call_count = 0

    def capabilities(self):
        from ppt_agent.llm.providers.base import ProviderCapabilities
        return ProviderCapabilities()

    def generate(self, messages, **kwargs):
        from ppt_agent.llm.messages import LLMResult
        idx = min(self._call_count, len(self._responses) - 1)
        text = self._responses[idx]
        self._call_count += 1
        return LLMResult(text=text, provider="test", model="test", prompt_tokens=10, completion_tokens=20)


class TestAgentLoop:
    """Tests for the AgentLoop base class."""

    def _make_loop(self, responses: list[str] | None = None, max_turns: int = 5):
        """Create a concrete AgentLoop for testing."""
        from ppt_agent.llm.client import LLMClient

        provider = DummyProvider(responses)
        client = LLMClient.from_provider(provider)

        class ConcreteAgent(AgentLoop):
            def build_system_prompt(self):
                return "You are a test agent."

            def get_available_tools(self):
                return [{"name": "test_tool", "description": "A test tool", "parameters": {"input": "string"}}]

            def execute_tool(self, tool_call):
                return ToolResult(call_id=tool_call.call_id, output={"echo": tool_call.arguments}, success=True)

            def parse_llm_response(self, result):
                from ppt_agent.llm.json_repair import repair_json
                try:
                    data, _warnings = repair_json(result.text)
                except Exception:
                    return result.text, [], False
                if not isinstance(data, dict):
                    return result.text, [], False
                if data.get("done"):
                    return result.text, [], True
                tool_name = data.get("tool")
                if tool_name:
                    tc = ToolCall(tool_name=tool_name, arguments=data.get("arguments", {}), call_id="tc1")
                    return result.text, [tc], False
                return result.text, [], False

        return ConcreteAgent("test-agent", client, max_turns=max_turns)

    def test_agent_completes_on_done_signal(self):
        agent = self._make_loop(['{"done": true, "result": "finished"}'])
        result = agent.run("Do something")
        assert result.stop_reason == StopReason.COMPLETED
        assert len(result.turns) == 1
        assert result.agent_id == "test-agent"

    def test_agent_executes_tool_then_completes(self):
        agent = self._make_loop([
            '{"tool": "test_tool", "arguments": {"input": "hello"}}',
            '{"done": true, "result": "completed after tool"}',
        ])
        result = agent.run("Use a tool then finish")
        assert result.stop_reason == StopReason.COMPLETED
        assert len(result.turns) == 2
        # First turn should have a tool call
        assert len(result.turns[0].tool_calls) == 1
        assert result.turns[0].tool_calls[0].tool_name == "test_tool"
        # First turn should have a tool result
        assert len(result.turns[0].tool_results) == 1
        assert result.turns[0].tool_results[0].success is True

    def test_agent_hits_max_turns(self):
        # Agent always calls a tool, never signals done
        responses = ['{"tool": "test_tool", "arguments": {}}'] * 10
        agent = self._make_loop(responses, max_turns=3)
        result = agent.run("Loop forever")
        assert result.stop_reason == StopReason.MAX_TURNS
        assert len(result.turns) == 3

    def test_agent_no_action_returns_text(self):
        agent = self._make_loop(["Just some text, no JSON"])
        result = agent.run("Say something")
        assert result.stop_reason == StopReason.NO_ACTION
        assert result.final_output == "Just some text, no JSON"

    def test_agent_abort(self):
        agent = self._make_loop(['{"tool": "test_tool", "arguments": {}}'] * 10)
        agent.abort()
        result = agent.run("Do something")
        assert result.stop_reason == StopReason.ABORTED

    def test_agent_states_lifecycle(self):
        agent = self._make_loop(['{"done": true}'])
        assert agent.state == AgentState.IDLE
        result = agent.run("Test")
        assert agent.state == AgentState.DONE


class TestTaskTypes:
    """Tests for the 7 task types and ID generation."""

    def test_seven_task_types_exist(self):
        assert len(TaskType) == 7

    def test_task_id_prefixes(self):
        assert make_task_id(TaskType.LOCAL_BASH, 1) == "b-0001"
        assert make_task_id(TaskType.LOCAL_AGENT, 42) == "a-0042"
        assert make_task_id(TaskType.REMOTE_AGENT, 3) == "r-0003"
        assert make_task_id(TaskType.IN_PROCESS_TEAMMATE, 1) == "t-0001"
        assert make_task_id(TaskType.LOCAL_WORKFLOW, 1) == "w-0001"
        assert make_task_id(TaskType.MONITOR_MCP, 1) == "m-0001"
        assert make_task_id(TaskType.DREAM, 1) == "d-0001"

    def test_legacy_task_id_backward_compat(self):
        """Legacy string kinds still work for backward compatibility."""
        assert make_task_id("worker", 1) == "work-0001"
        assert make_task_id("coordinator", 5) == "coord-0005"
        assert make_task_id("tool", 3) == "tool-0003"

    def test_invalid_task_kind_raises(self):
        with pytest.raises(ValueError):
            make_task_id("nonexistent", 1)

    def test_valid_transitions(self):
        assert validate_transition(TaskStatus.PENDING, TaskStatus.RUNNING) is True
        assert validate_transition(TaskStatus.RUNNING, TaskStatus.COMPLETED) is True
        assert validate_transition(TaskStatus.RUNNING, TaskStatus.FAILED) is True
        assert validate_transition(TaskStatus.RUNNING, TaskStatus.WAITING) is True
        assert validate_transition(TaskStatus.WAITING, TaskStatus.RUNNING) is True

    def test_invalid_transitions(self):
        assert validate_transition(TaskStatus.COMPLETED, TaskStatus.RUNNING) is False
        assert validate_transition(TaskStatus.FAILED, TaskStatus.RUNNING) is False
        assert validate_transition(TaskStatus.PENDING, TaskStatus.COMPLETED) is False


class TestTaskManager:
    """Tests for the enhanced TaskManager."""

    def test_create_and_get(self):
        mgr = TaskManager()
        task = mgr.create(TaskType.LOCAL_AGENT, description="test task")
        assert task.task_id == "a-0001"
        assert task.task_type == TaskType.LOCAL_AGENT
        assert task.status == TaskStatus.PENDING
        assert mgr.get(task.task_id) is task

    def test_parent_child_relationship(self):
        mgr = TaskManager()
        parent = mgr.create(TaskType.LOCAL_WORKFLOW, description="parent")
        child = mgr.create(TaskType.LOCAL_AGENT, parent_id=parent.task_id, description="child")
        assert child.parent_id == parent.task_id
        assert child.task_id in parent.children

    def test_transition_lifecycle(self):
        mgr = TaskManager()
        task = mgr.create(TaskType.LOCAL_AGENT)
        mgr.transition(task.task_id, TaskStatus.RUNNING)
        assert task.status == TaskStatus.RUNNING
        assert task.started_at is not None
        mgr.transition(task.task_id, TaskStatus.COMPLETED)
        assert task.status == TaskStatus.COMPLETED
        assert task.completed_at is not None

    def test_invalid_transition_raises(self):
        mgr = TaskManager()
        task = mgr.create(TaskType.LOCAL_AGENT)
        with pytest.raises(ValueError):
            mgr.transition(task.task_id, TaskStatus.COMPLETED)  # Can't go PENDING → COMPLETED

    def test_list_by_type(self):
        mgr = TaskManager()
        mgr.create(TaskType.LOCAL_AGENT, description="a1")
        mgr.create(TaskType.LOCAL_AGENT, description="a2")
        mgr.create(TaskType.DREAM, description="d1")
        assert len(mgr.list_by_type(TaskType.LOCAL_AGENT)) == 2
        assert len(mgr.list_by_type(TaskType.DREAM)) == 1

    def test_all_completed(self):
        mgr = TaskManager()
        parent = mgr.create(TaskType.LOCAL_WORKFLOW, description="parent")
        c1 = mgr.create(TaskType.LOCAL_AGENT, parent_id=parent.task_id)
        c2 = mgr.create(TaskType.LOCAL_AGENT, parent_id=parent.task_id)

        assert not mgr.all_completed(parent.task_id)

        mgr.transition(c1.task_id, TaskStatus.RUNNING)
        mgr.transition(c1.task_id, TaskStatus.COMPLETED)
        assert not mgr.all_completed(parent.task_id)

        mgr.transition(c2.task_id, TaskStatus.RUNNING)
        mgr.transition(c2.task_id, TaskStatus.FAILED)
        assert mgr.all_completed(parent.task_id)  # FAILED is also terminal

    def test_active_count(self):
        mgr = TaskManager()
        t1 = mgr.create(TaskType.LOCAL_AGENT)
        t2 = mgr.create(TaskType.LOCAL_AGENT)
        assert mgr.active_count == 0
        mgr.transition(t1.task_id, TaskStatus.RUNNING)
        assert mgr.active_count == 1
        mgr.transition(t2.task_id, TaskStatus.RUNNING)
        mgr.transition(t2.task_id, TaskStatus.WAITING)
        assert mgr.active_count == 2


class TestMailbox:
    """Tests for the enhanced Mailbox with locking and priority."""

    def test_append_and_read(self, tmp_path: Path):
        box = Mailbox(tmp_path / "inbox.jsonl")
        box.append("agent-a", "agent-b", "hello", ["artifact.json"])
        msgs = box.read_all()
        assert len(msgs) == 1
        assert msgs[0]["sender"] == "agent-a"
        assert msgs[0]["message"] == "hello"
        assert msgs[0]["artifact_refs"] == ["artifact.json"]
        assert msgs[0]["read"] is False

    def test_priority_ordering(self, tmp_path: Path):
        box = Mailbox(tmp_path / "inbox.jsonl")
        box.append("peer", "me", "low prio", priority=MessagePriority.BACKGROUND)
        box.append("lead", "me", "high prio", priority=MessagePriority.TEAM_LEAD)
        box.append("system", "me", "shutdown", priority=MessagePriority.SHUTDOWN)
        box.append("peer2", "me", "normal", priority=MessagePriority.PEER)

        msgs = box.read_all()
        assert msgs[0]["priority"] == MessagePriority.SHUTDOWN
        assert msgs[1]["priority"] == MessagePriority.TEAM_LEAD
        assert msgs[2]["priority"] == MessagePriority.PEER
        assert msgs[3]["priority"] == MessagePriority.BACKGROUND

    def test_unread_filtering(self, tmp_path: Path):
        box = Mailbox(tmp_path / "inbox.jsonl")
        box.append("a", "b", "msg1")
        box.append("a", "b", "msg2")

        assert len(box.read_unread()) == 2
        box.mark_read()
        assert len(box.read_unread()) == 0
        assert len(box.read_all()) == 2  # All still there

    def test_mark_read_by_sender(self, tmp_path: Path):
        box = Mailbox(tmp_path / "inbox.jsonl")
        box.append("alice", "me", "from alice")
        box.append("bob", "me", "from bob")
        box.mark_read(sender="alice")
        unread = box.read_unread()
        assert len(unread) == 1
        assert unread[0]["sender"] == "bob"

    def test_shutdown_detection(self, tmp_path: Path):
        box = Mailbox(tmp_path / "inbox.jsonl")
        assert not box.has_shutdown_request()
        box.append("system", "me", "shutdown", priority=MessagePriority.SHUTDOWN)
        assert box.has_shutdown_request()

    def test_pending_count(self, tmp_path: Path):
        box = Mailbox(tmp_path / "inbox.jsonl")
        assert box.pending_count() == 0
        box.append("a", "b", "msg1")
        box.append("a", "b", "msg2")
        assert box.pending_count() == 2


class TestContextCompressionInLLMClient:
    """Tests for the 5-level context compression wired into the LLM client."""

    def test_compression_auto_level_detection(self):
        from ppt_agent.llm.client import LLMClient
        from ppt_agent.llm.providers.fake import FakeProvider
        from ppt_agent.llm.config import ProviderConfig

        cfg = ProviderConfig(name="fake", protocol="fake", text_model="fake")
        provider = FakeProvider(cfg)
        client = LLMClient(provider, context_window=1000)

        # Small context → L0 (no compression)
        level = client._determine_compression_level("system", "prompt", {"key": "val"})
        assert level == 0

        # Large context → higher compression
        big_context = {"data": "x" * 3000}
        level = client._determine_compression_level("system", "prompt", big_context)
        assert level >= 1

    def test_memory_context_injected_into_system_prompt(self):
        from ppt_agent.llm.client import LLMClient
        from ppt_agent.llm.providers.fake import FakeProvider
        from ppt_agent.llm.config import ProviderConfig

        cfg = ProviderConfig(name="fake", protocol="fake", text_model="fake")
        provider = FakeProvider(cfg)
        client = LLMClient(provider)

        # Set memory context
        client._memory_context = "User prefers dark themes."

        messages = client._build_messages("test prompt", None, "Base system")
        assert len(messages) == 2  # system + user
        system_text = messages[0].content[0].text
        assert "Agent Memory" in system_text
        assert "User prefers dark themes" in system_text

    def test_skill_context_injected_into_system_prompt(self):
        from ppt_agent.llm.client import LLMClient
        from ppt_agent.llm.providers.fake import FakeProvider
        from ppt_agent.llm.config import ProviderConfig

        cfg = ProviderConfig(name="fake", protocol="fake", text_model="fake")
        provider = FakeProvider(cfg)
        client = LLMClient(provider)

        client._skill_contexts = {
            "outline_generation": {
                "skill_name": "ppt-outline-generator",
                "content": "Skill instructions here.",
            }
        }

        messages = client._build_messages("test prompt", None, "Base system")
        system_text = messages[0].content[0].text
        assert "ppt-outline-generator" in system_text
        assert "Skill instructions here" in system_text

    def test_micro_repair_omits_large_phase_skills(self):
        from ppt_agent.llm.client import LLMClient
        from ppt_agent.llm.providers.fake import FakeProvider
        from ppt_agent.llm.config import ProviderConfig

        cfg = ProviderConfig(name="fake", protocol="fake", text_model="fake")
        client = LLMClient(FakeProvider(cfg))
        client._skill_contexts = {
            "outline_generation": {
                "skill_name": "outline-skill",
                "content": "Large outline instructions.",
            },
            "content_mapping": {
                "skill_name": "mapping-skill",
                "content": "Mapping instructions.",
            },
        }

        messages = client._build_messages(
            "repair one zone",
            None,
            "Base system",
            phase="content_mapping_micro_3_1",
        )
        system_text = messages[0].content[0].text

        assert "mapping-skill" not in system_text
        assert "outline-skill" not in system_text


class TestCoordinatorToolEnforcement:
    """Test that coordinator has exactly 4 tools and workers are restricted."""

    def test_coordinator_has_exactly_4_tools(self):
        from ppt_agent.tools.registry import create_default_registry, ROLE_COORDINATOR
        registry = create_default_registry()
        coord_tools = registry.list_tools(ROLE_COORDINATOR)
        assert len(coord_tools) == 4
        tool_names = {t.name for t in coord_tools}
        assert tool_names == {"AgentTool", "TaskStopTool", "SendMessageTool", "SyntheticOutput"}

    def test_coordinator_cannot_use_worker_tools(self):
        from ppt_agent.tools.registry import create_default_registry, ROLE_COORDINATOR
        registry = create_default_registry()
        with pytest.raises(PermissionError):
            registry.check_permission("FileSystemTool", ROLE_COORDINATOR)

    def test_worker_cannot_use_coordinator_tools(self):
        from ppt_agent.tools.registry import create_default_registry, ROLE_WORKER
        registry = create_default_registry()
        with pytest.raises(PermissionError):
            registry.check_permission("AgentTool", ROLE_WORKER)


class TestWorkflowModes:
    """Test that workflow correctly routes to agent vs deterministic mode."""

    def test_deterministic_mode_without_llm(self):
        """Without model_profile, should use deterministic linear pipeline."""
        from ppt_agent.coordinator.phase_state import create_job
        from ppt_agent.coordinator.workflow import run_workflow

        with tempfile.TemporaryDirectory() as tmpdir:
            input_dir = Path(tmpdir) / "input"
            input_dir.mkdir()
            (input_dir / "test.md").write_text("# Test\n\nContent.", encoding="utf-8")

            job_dir = Path(tmpdir) / "job"
            workspace = create_job(input_dir, job_dir)
            outputs = run_workflow(workspace, force=True)

            assert (job_dir / "final.pptx").exists()
            assert (job_dir / "source_summary.json").exists()

    def test_fake_provider_uses_llm_augmented_mode(self):
        """With model_profile='fake', should use LLM-augmented deterministic mode."""
        from ppt_agent.coordinator.phase_state import create_job
        from ppt_agent.coordinator.workflow import run_workflow

        with tempfile.TemporaryDirectory() as tmpdir:
            input_dir = Path(tmpdir) / "input"
            input_dir.mkdir()
            (input_dir / "test.md").write_text("# Test\n\nContent.", encoding="utf-8")

            job_dir = Path(tmpdir) / "job"
            workspace = create_job(input_dir, job_dir)
            outputs = run_workflow(workspace, model_profile="fake", force=True)

            assert (job_dir / "final.pptx").exists()
            assert (job_dir / "source_summary.json").exists()
            assert (job_dir / "outline.json").exists()
