"""Tests for Phase 2 AgentLoop upgrades — mailbox, compression, skill loading,
ConversationStore integration, and backward compatibility.

Uses mocks for platform-specific modules (Mailbox requires fcntl on POSIX,
ConversationStore is tested separately in test_conversation_store.py).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ppt_agent.llm.messages import LLMMessage, LLMResult
from ppt_agent.runtime.agent_loop import (
    AgentLoop,
    AgentLoopResult,
    StopReason,
    ToolCall,
    ToolResult,
)
from ppt_agent.skills.loader import SkillLoader


# ── Helpers ──────────────────────────────────────────────────────────

def _make_mock_mailbox(messages: list[dict] | None = None):
    """Create a mock Mailbox that doesn't import fcntl."""
    box = MagicMock()
    box.read_unread.return_value = messages or []
    box.mark_read.return_value = 0
    box.has_shutdown_request.return_value = any(
        m.get("priority", 3) == 1 for m in (messages or [])
    )
    return box


def _make_mock_store():
    """Create a mock ConversationStore."""
    store = MagicMock()
    store.load_messages.return_value = []
    store.load_entries.return_value = []
    store.get_stats.return_value = {"message_count": 0}
    return store


# ── Minimal concrete AgentLoop for testing ──────────────────────────

class _TestAgent(AgentLoop):
    """Concrete AgentLoop that returns canned responses for testing."""

    def __init__(self, **kwargs):
        super().__init__(agent_id="test-agent", llm_client=MagicMock(), **kwargs)
        self._next_responses: list[LLMResult] = []

    def set_response(self, text: str = "", tool_name: str = "",
                     arguments: dict | None = None, done: bool = False) -> None:
        self._next_responses.append(LLMResult(
            text=text, provider="fake", model="fake",
            json_data=({"tool": tool_name, "arguments": arguments or {}} if tool_name else (
                {"done": True, "result": text} if done else None
            )),
        ))

    def build_system_prompt(self) -> str:
        return "You are a test agent."

    def get_available_tools(self) -> list[dict]:
        return [
            {"name": "test_tool", "description": "A test tool.", "parameters": {}},
            {"name": "load_skill", "description": "Load a skill into context.", "parameters": {}},
        ]

    def execute_tool(self, tool_call: ToolCall) -> ToolResult:
        return ToolResult(call_id=tool_call.call_id, output={"ok": True}, success=True)

    def parse_llm_response(self, result: LLMResult) -> tuple[str, list[ToolCall], bool]:
        text = result.text
        data = result.json_data
        if data is None:
            return text, [], False
        if data.get("done"):
            return text, [], True
        tool = data.get("tool")
        if tool:
            return text, [ToolCall(tool_name=tool, arguments=data.get("arguments", {}))], False
        return text, [], False

    def _call_llm(self):
        """Override to return canned responses — bypasses real LLM."""
        if self._next_responses:
            return self._next_responses.pop(0)
        return LLMResult(text="done", json_data={"done": True, "result": "done"})


# ── Backward compatibility ───────────────────────────────────────────

def test_agent_loop_without_new_params_behaves_identically() -> None:
    """All new params default to None; agent still runs the core cycle."""
    agent = _TestAgent(max_turns=3)
    agent.set_response(tool_name="test_tool", arguments={})
    agent.set_response(done=True, text="completed")

    result = agent.run("test task")

    assert result.stop_reason == StopReason.COMPLETED
    assert len(result.turns) == 2
    assert agent.conversation_store is None
    assert agent.mailbox is None
    assert agent.tool_registry is None
    assert agent.skill_loader is None
    assert agent.context_window_limit == 128_000


def test_agent_loop_no_action_stops() -> None:
    """Agent produces text without tool calls or done → stops with NO_ACTION."""
    agent = _TestAgent(max_turns=3)
    agent.set_response(text="Just some reasoning text, no tool call.")

    result = agent.run("test task")
    assert result.stop_reason == StopReason.NO_ACTION
    assert result.final_output == "Just some reasoning text, no tool call."


def test_load_skill_builtin_tool_works_in_execute_turn() -> None:
    """load_skill is intercepted as a built-in tool in _execute_turn."""
    loader = SkillLoader()  # no skills directory → load_skill returns None
    agent = _TestAgent(skill_loader=loader)
    agent.messages = [LLMMessage.system("sys"), LLMMessage.user("task")]

    # Simulate LLM requesting load_skill
    agent._next_responses.append(LLMResult(
        text="loading skill",
        json_data={"tool": "load_skill", "arguments": {"skill_name": "nonexistent"}},
    ))
    agent._next_responses.append(LLMResult(
        text="done",
        json_data={"done": True, "result": "tried"},
    ))

    result = agent.run("task")
    assert result.stop_reason == StopReason.COMPLETED
    # First turn should have tried load_skill (which failed but didn't crash)
    assert len(result.turns) >= 1


# ── _estimate_tokens ─────────────────────────────────────────────────

def test_estimate_tokens_empty() -> None:
    agent = _TestAgent()
    assert agent._estimate_tokens([]) == 0


def test_estimate_tokens_counts_chars() -> None:
    agent = _TestAgent()
    msgs = [LLMMessage.system("abcd" * 10)]  # 40 chars
    assert agent._estimate_tokens(msgs) == 10  # 40 / 4


def test_estimate_tokens_multi_message() -> None:
    agent = _TestAgent()
    msgs = [
        LLMMessage.system("a" * 400),
        LLMMessage.user("b" * 400),
        LLMMessage.assistant("c" * 200),
    ]
    assert agent._estimate_tokens(msgs) == 250  # 1000 / 4


def test_estimate_tokens_handles_none_text() -> None:
    """ContentBlock with text=None should not crash."""
    agent = _TestAgent()
    from ppt_agent.llm.messages import ContentBlock
    msg = LLMMessage(role="user", content=[ContentBlock(type="text", text=None)])
    assert agent._estimate_tokens([msg]) == 0


# ── _compress_messages ──────────────────────────────────────────────

def test_compress_noop_when_under_threshold() -> None:
    """L0: utilization < 60% → no compression."""
    agent = _TestAgent(context_window_limit=100_000)
    agent.messages = [
        LLMMessage.system("short prompt"),
        LLMMessage.user("short message"),
    ]
    original = [m.content[0].text for m in agent.messages]
    agent._compress_messages()
    assert len(agent.messages) == len(original)
    for i, m in enumerate(agent.messages):
        assert m.content[0].text == original[i]


def test_compress_l1_truncates_large_tool_outputs() -> None:
    """L1: 60-75% utilization → truncate large tool results in compressible range."""
    # Need ≥5 messages so the tool result falls in messages[1:-2] (compressible)
    # 1200 tokens limit; total ~4025 chars = ~1006 tokens → 83.9% (L2 actually!)
    # Use 1500 tokens → 67.1% → L1
    agent = _TestAgent(context_window_limit=1500)
    long_output = "x" * 3000
    agent.messages = [
        LLMMessage.system("sys " * 50),            # index 0: system (invariant)
        LLMMessage.user("old message 1 " * 20),     # index 1: compressible
        LLMMessage.user(f"[Tool Result: some_tool]\n{long_output}"),  # index 2: compressible
        LLMMessage.user("old message 3 " * 20),     # index 3: compressible
        LLMMessage.user("recent 1"),                # index -2: recent (invariant)
        LLMMessage.user("recent 2"),                # index -1: recent (invariant)
    ]
    agent._compress_messages()
    # Find the message that had the tool result
    tool_msg = next((m for m in agent.messages if m.content[0].text and "[Tool Result:" in m.content[0].text), None)
    assert tool_msg is not None, "Tool result message was removed (should be preserved+truncated)"
    text = tool_msg.content[0].text or ""
    assert "truncated" in text.lower(), f"Expected 'truncated' in compressed text, got {len(text)} chars"
    assert len(text) < len(long_output) + 100


def test_compress_l2_uses_llm_summary() -> None:
    """L2: 75-85% utilization → LLM-summarise first half of compressible msgs."""
    agent = _TestAgent(context_window_limit=2000)
    agent.messages = [LLMMessage.system("sys")]
    for i in range(20):
        agent.messages.append(LLMMessage.user(f"msg {i} " * 50))
    agent.messages.append(LLMMessage.user("recent 1"))
    agent.messages.append(LLMMessage.user("recent 2"))

    original_count = len(agent.messages)
    with patch("ppt_agent.runtime.agent_loop._llm_summarize", return_value="summarized content"):
        agent._compress_messages()

    assert len(agent.messages) < original_count
    assert agent.messages[0].content[0].text == "sys"
    assert agent.messages[-2].content[0].text == "recent 1"
    assert agent.messages[-1].content[0].text == "recent 2"


def test_compress_l3_keeps_only_system_errors_recent() -> None:
    """L3: 85-95% → system + errors + last 4."""
    # 200 token limit → 800 chars; messages ~1239 chars → ~310 tokens → 155% utilization → L4
    # Use larger window to hit L3 instead of L4
    agent = _TestAgent(context_window_limit=350)
    agent.messages = [
        LLMMessage.system("sys"),
        LLMMessage.user("old 1 " * 50),
        LLMMessage.user("old 2 " * 50),
        LLMMessage.user("old 3 " * 50),
        LLMMessage.user("old 4 " * 50),
        LLMMessage.user("old 5 " * 50),
        LLMMessage.user("recent 1"),
        LLMMessage.user("recent 2"),
        LLMMessage.user("recent 3"),
        LLMMessage.user("recent 4"),
    ]
    agent._compress_messages()
    assert agent.messages[0].content[0].text == "sys"
    assert len(agent.messages) <= 7  # system + errors + up to 5 more


def test_compress_l4_emergency_mode() -> None:
    """L4: > 95% → system + last 2 only."""
    # 40 token limit → 160 chars; messages ~1021 chars → 255 tokens → 637% → L4
    agent = _TestAgent(context_window_limit=40)
    agent.messages = [
        LLMMessage.system("sys"),
        LLMMessage.user("a" * 500),
        LLMMessage.user("b" * 500),
        LLMMessage.user("recent 1"),
        LLMMessage.user("recent 2"),
    ]
    agent._compress_messages()
    assert len(agent.messages) == 3
    assert agent.messages[0].content[0].text == "sys"
    assert agent.messages[-1].content[0].text == "recent 2"


def test_compress_preserves_error_messages() -> None:
    """Messages containing [Error or [Tool Error are preserved in L2-L3."""
    agent = _TestAgent(context_window_limit=2000)
    agent.messages = [
        LLMMessage.system("sys"),
        LLMMessage.user("normal 1 " * 50),
        LLMMessage.user("[Tool Error: search]\nSomething went wrong"),
        LLMMessage.user("normal 2 " * 50),
        LLMMessage.user("normal 3 " * 50),
        LLMMessage.user("recent 1"),
        LLMMessage.user("recent 2"),
    ]
    with patch("ppt_agent.runtime.agent_loop._llm_summarize", return_value="summarized"):
        agent._compress_messages()

    texts = [m.content[0].text or "" for m in agent.messages]
    assert any("[Tool Error" in t for t in texts), f"Error message not preserved in: {texts}"


# ── _load_skill_tool ────────────────────────────────────────────────

def test_load_skill_tool_first_load(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Test Skill\nContent here.", encoding="utf-8")

    loader = SkillLoader(root=tmp_path / "skills")
    agent = _TestAgent(skill_loader=loader)
    agent.messages = [LLMMessage.system("base prompt")]

    result = agent._load_skill_tool("test-skill")

    assert result.success
    assert result.output["status"] == "loaded"
    assert result.output["skill"] == "test-skill"
    assert "test-skill" in agent._loaded_skills
    # System prompt should contain skill content
    assert "Test Skill" in agent.messages[0].content[0].text


def test_load_skill_tool_duplicate() -> None:
    loader = SkillLoader()
    agent = _TestAgent(skill_loader=loader)
    agent.messages = [LLMMessage.system("base")]
    agent._loaded_skills.add("already-loaded")

    result = agent._load_skill_tool("already-loaded")
    assert result.success
    assert result.output["status"] == "already_loaded"


def test_load_skill_tool_missing() -> None:
    loader = SkillLoader()
    agent = _TestAgent(skill_loader=loader)

    result = agent._load_skill_tool("nonexistent")
    assert not result.success
    assert "not found" in (result.error or "")


def test_load_skill_tool_empty_name() -> None:
    agent = _TestAgent()
    result = agent._load_skill_tool("")
    assert not result.success
    assert "required" in (result.error or "")


def test_load_skill_tool_no_loader() -> None:
    agent = _TestAgent()
    result = agent._load_skill_tool("any-skill")
    assert not result.success
    assert "No skill loader" in (result.error or "")


# ── ConversationStore integration ────────────────────────────────────

def test_run_restores_history_from_store() -> None:
    store = _make_mock_store()
    store.load_messages.return_value = [
        LLMMessage.user("prior question"),
        LLMMessage.assistant("prior answer"),
    ]
    agent = _TestAgent(conversation_store=store, max_turns=2)
    agent.set_response(done=True, text="completed")

    result = agent.run("new task")
    assert result.stop_reason == StopReason.COMPLETED

    # Verify history was restored
    texts = [m.content[0].text or "" for m in agent.messages]
    assert "prior question" in texts[1]  # after system prompt
    assert "prior answer" in texts[2]

    # Verify new messages were appended to store (at least the assistant response)
    assert store.append.call_count >= 1


def test_run_does_not_append_when_no_store() -> None:
    agent = _TestAgent()  # no conversation_store
    agent.set_response(done=True, text="done")
    result = agent.run("task")
    assert result.stop_reason == StopReason.COMPLETED


# ── Mailbox integration ─────────────────────────────────────────────

def test_check_mailbox_injects_messages() -> None:
    box = _make_mock_mailbox([
        {"sender": "coordinator", "recipient": "test-agent",
         "message": "Please check slide 3.", "priority": 3, "read": False},
    ])
    agent = _TestAgent(mailbox=box, max_turns=2)
    agent.messages = [LLMMessage.system("sys"), LLMMessage.user("task")]
    agent.set_response(done=True, text="will check")
    agent._check_mailbox()

    all_text = " ".join(m.content[0].text or "" for m in agent.messages)
    assert "slide 3" in all_text


def test_check_mailbox_shutdown_aborts() -> None:
    box = _make_mock_mailbox([
        {"sender": "coordinator", "recipient": "test-agent",
         "message": "Stop immediately.", "priority": 1, "read": False},
    ])
    agent = _TestAgent(mailbox=box, max_turns=10)
    agent.messages = [LLMMessage.system("sys"), LLMMessage.user("task")]

    agent._check_mailbox()
    assert agent._aborted


def test_check_mailbox_no_mailbox_is_noop() -> None:
    agent = _TestAgent()
    agent.messages = [LLMMessage.system("sys"), LLMMessage.user("task")]
    agent._check_mailbox()
    assert len(agent.messages) == 2


def test_check_mailbox_empty_is_noop() -> None:
    box = _make_mock_mailbox([])
    agent = _TestAgent(mailbox=box)
    agent.messages = [LLMMessage.system("sys"), LLMMessage.user("task")]
    agent._check_mailbox()
    assert len(agent.messages) == 2


def test_check_mailbox_exception_is_graceful() -> None:
    """If mailbox.read_unread() throws, agent continues without crashing."""
    box = MagicMock()
    box.read_unread.side_effect = OSError("disk full")
    agent = _TestAgent(mailbox=box)
    agent.messages = [LLMMessage.system("sys"), LLMMessage.user("task")]

    # Should not raise
    agent._check_mailbox()
    assert len(agent.messages) == 2


# ── Context window ──────────────────────────────────────────────────

def test_context_window_limit_default() -> None:
    agent = _TestAgent()
    assert agent.context_window_limit == 128_000


def test_context_window_limit_custom() -> None:
    agent = _TestAgent(context_window_limit=8000)
    assert agent.context_window_limit == 8000


# ── Edge cases ──────────────────────────────────────────────────────

def test_compress_single_message_does_not_crash() -> None:
    """Single message (only system prompt) — no compression panic."""
    agent = _TestAgent(context_window_limit=100)
    agent.messages = [LLMMessage.system("short")]
    agent._compress_messages()
    assert len(agent.messages) == 1


def test_compress_two_messages_preserved() -> None:
    """Two messages (sys + user) — both recent, both kept."""
    agent = _TestAgent(context_window_limit=100)
    agent.messages = [
        LLMMessage.system("sys"),
        LLMMessage.user("u"),
    ]
    agent._compress_messages()
    assert len(agent.messages) == 2


def test_run_with_zero_max_turns_stops_immediately() -> None:
    agent = _TestAgent(max_turns=0)
    result = agent.run("task")
    assert result.stop_reason == StopReason.MAX_TURNS
    assert len(result.turns) == 0


def test_compress_l2_fallback_when_llm_fails() -> None:
    """L2 compression falls back to truncation when LLM summarization fails."""
    agent = _TestAgent(context_window_limit=2000)
    agent.messages = [LLMMessage.system("sys")]
    for i in range(20):
        agent.messages.append(LLMMessage.user(f"msg {i} " * 50))
    agent.messages.append(LLMMessage.user("recent 1"))
    agent.messages.append(LLMMessage.user("recent 2"))

    original_count = len(agent.messages)
    # _llm_summarize returns empty string → fallback to truncation
    with patch("ppt_agent.runtime.agent_loop._llm_summarize", return_value=""):
        agent._compress_messages()

    assert len(agent.messages) < original_count
    assert agent.messages[-1].content[0].text == "recent 2"


def test_compress_div_by_zero_protection() -> None:
    """context_window_limit=0 should not cause ZeroDivisionError."""
    agent = _TestAgent(context_window_limit=0)
    agent.messages = [
        LLMMessage.system("sys"),
        LLMMessage.user("hello"),
    ]
    # Should not raise — max(limit, 1) protects
    agent._compress_messages()


def test_compress_preserves_system_prompt_always() -> None:
    """System prompt is ALWAYS the first message after ANY compression level."""
    agent = _TestAgent(context_window_limit=400)
    agent.messages = [
        LLMMessage.system("IMPORTANT SYSTEM IDENTITY"),
        LLMMessage.user("a" * 500),
        LLMMessage.user("b" * 500),
        LLMMessage.user("c" * 500),
        LLMMessage.user("d" * 500),
    ]
    agent._compress_messages()
    assert agent.messages[0].content[0].text == "IMPORTANT SYSTEM IDENTITY"


# ── is_loaded on SkillLoader ────────────────────────────────────────

def test_skill_loader_is_loaded() -> None:
    loader = SkillLoader()
    assert not loader.is_loaded("any-skill")
    loader._loaded.add("any-skill")
    assert loader.is_loaded("any-skill")


def test_skill_loader_is_loaded_false_for_unknown() -> None:
    loader = SkillLoader()
    assert not loader.is_loaded("never-loaded")
