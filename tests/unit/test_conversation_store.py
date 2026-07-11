"""Tests for ConversationStore — JSONL conversation persistence.

Covers: append, load_messages, load_entries, get_stats, thread safety,
ContentBlock round-trip fidelity, corrupt line handling, and edge cases.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from ppt_agent.llm.messages import ContentBlock, LLMMessage
from ppt_agent.runtime.conversation_store import ConversationEntry, ConversationStore


# ── Helpers ──────────────────────────────────────────────────────────

def _make_message(role: str, text: str) -> LLMMessage:
    if role == "system":
        return LLMMessage.system(text)
    if role == "user":
        return LLMMessage.user(text)
    return LLMMessage.assistant(text)


# ── Append ───────────────────────────────────────────────────────────

def test_append_writes_valid_json_line(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    msg = LLMMessage.user("Hello, world!")
    entry = store.append(msg, node_id="n1", agent_id="test-agent")

    assert store.history_path.exists()
    lines = store.history_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1

    data = json.loads(lines[0])
    assert data["role"] == "user"
    assert data["content"] == "Hello, world!"
    assert data["node_id"] == "n1"
    assert data["agent_id"] == "test-agent"
    assert "id" in data
    assert "timestamp" in data
    # Content blocks should be serialized
    assert "content_blocks" in data
    blocks = json.loads(data["content_blocks"])
    assert len(blocks) == 1
    assert blocks[0]["type"] == "text"
    assert blocks[0]["text"] == "Hello, world!"


def test_append_returns_valid_entry(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    msg = LLMMessage.assistant("response")
    entry = store.append(msg, node_id="n2")

    assert isinstance(entry, ConversationEntry)
    assert entry.role == "assistant"
    assert entry.content == "response"
    assert entry.node_id == "n2"


def test_append_auto_generates_node_id(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    msg = LLMMessage.system("system prompt")
    entry = store.append(msg)

    assert entry.node_id is not None
    assert entry.node_id.startswith("msg_")


def test_append_multiple_messages(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    for i in range(10):
        store.append(LLMMessage.user(f"message {i}"), node_id=f"n{i}")

    lines = store.history_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 10
    for line in lines:
        data = json.loads(line)
        assert "id" in data
        assert "role" in data
        assert "content" in data


# ── ContentBlock round-trip fidelity ────────────────────────────────

def test_text_blocks_preserve_type(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    msg = LLMMessage(role="user", content=[
        ContentBlock(type="text", text="plain text"),
        ContentBlock(type="json", text='{"key":"val"}'),
    ])
    store.append(msg, node_id="n1")

    loaded = store.load_messages()
    assert len(loaded) == 1
    blocks = loaded[0].content
    assert len(blocks) == 2
    assert blocks[0].type == "text"
    assert blocks[0].text == "plain text"
    assert blocks[1].type == "json"
    assert blocks[1].text == '{"key":"val"}'


def test_image_blocks_preserved_on_round_trip(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    msg = LLMMessage(role="user", content=[
        ContentBlock.text_block("look at this"),
        ContentBlock(type="image", path="photo.png", mime_type="image/png"),
    ])
    store.append(msg, node_id="n1")

    loaded = store.load_messages()
    assert len(loaded) == 1
    blocks = loaded[0].content
    assert len(blocks) == 2
    assert blocks[0].type == "text"
    assert blocks[1].type == "image"
    assert blocks[1].path == "photo.png"
    assert blocks[1].mime_type == "image/png"


def test_legacy_entries_without_content_blocks_fallback(tmp_path: Path) -> None:
    """Old-format entries (no content_blocks field) load as text-only."""
    store = ConversationStore(tmp_path)
    # Write a legacy-format line directly
    legacy = json.dumps({
        "id": "msg_old", "role": "user", "content": "legacy text",
        "timestamp": 1.0, "node_id": "n1",
    })
    store.history_path.write_text(legacy + "\n", encoding="utf-8")

    loaded = store.load_messages()
    assert len(loaded) == 1
    assert loaded[0].content[0].type == "text"
    assert loaded[0].content[0].text == "legacy text"


# ── Load messages ───────────────────────────────────────────────────

def test_load_messages_returns_llm_messages(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    store.append(LLMMessage.system("you are helpful"), node_id="n0")
    store.append(LLMMessage.user("question"), node_id="n1")
    store.append(LLMMessage.assistant("answer"), node_id="n2")

    messages = store.load_messages()
    assert len(messages) == 3
    assert messages[0].role == "system"
    assert messages[0].content[0].text == "you are helpful"
    assert messages[1].role == "user"
    assert messages[2].role == "assistant"


def test_load_messages_with_limit(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    for i in range(20):
        store.append(LLMMessage.user(f"msg {i}"), node_id=f"n{i}")

    limited = store.load_messages(limit=5)
    assert len(limited) == 5
    assert limited[0].content[0].text == "msg 15"
    assert limited[-1].content[0].text == "msg 19"


def test_load_messages_empty_store(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    assert store.load_messages() == []
    assert store.load_messages(limit=5) == []


def test_load_messages_limit_greater_than_count(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    store.append(LLMMessage.user("only"), node_id="n0")
    msgs = store.load_messages(limit=100)
    assert len(msgs) == 1


# ── Load entries ────────────────────────────────────────────────────

def test_load_entries_preserves_metadata(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    store.append(
        LLMMessage.user("test"),
        node_id="n1",
        parent_id="n0",
        tool_calls=[{"name": "search", "args": {}}],
        agent_id="agent-1",
        metadata={"key": "value"},
    )

    entries = store.load_entries()
    assert len(entries) == 1
    e = entries[0]
    assert e.role == "user"
    assert e.content == "test"
    assert e.node_id == "n1"
    assert e.parent_id == "n0"
    assert e.tool_calls == [{"name": "search", "args": {}}]
    assert e.agent_id == "agent-1"
    assert e.metadata == {"key": "value"}


# ── Stats ────────────────────────────────────────────────────────────

def test_get_stats_empty(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    stats = store.get_stats()
    assert stats["message_count"] == 0
    assert stats["total_chars"] == 0
    assert stats["oldest_timestamp"] is None


def test_get_stats_with_messages(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    store.append(LLMMessage.system("sys"), node_id="n0")
    store.append(LLMMessage.user("hello"), node_id="n1")
    store.append(LLMMessage.user("world"), node_id="n2")
    store.append(LLMMessage.assistant("reply"), node_id="n3")

    stats = store.get_stats()
    assert stats["message_count"] == 4
    assert stats["total_chars"] == len("sys") + len("hello") + len("world") + len("reply")
    assert stats["roles"] == {"system": 1, "user": 2, "assistant": 1}
    assert isinstance(stats["oldest_timestamp"], float)
    assert stats["newest_timestamp"] >= stats["oldest_timestamp"]


# ── Thread safety ───────────────────────────────────────────────────

def test_concurrent_appends_produce_valid_jsonl(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    errors: list[Exception] = []

    def _append_batch(start: int) -> None:
        try:
            for i in range(start, start + 50):
                store.append(LLMMessage.user(f"thread-msg-{i}"), node_id=f"n{i}")
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=_append_batch, args=(0,))
    t2 = threading.Thread(target=_append_batch, args=(50,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"Errors: {errors}"
    lines = store.history_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 100

    for i, line in enumerate(lines):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            pytest.fail(f"Line {i} invalid JSON: {line[:80]}")
        assert "id" in data
        assert "role" in data


def test_concurrent_appends_consistent_count(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)

    def _append_100() -> None:
        for i in range(100):
            store.append(LLMMessage.user(f"x-{i}"), node_id=f"x{i}")

    threads = [threading.Thread(target=_append_100) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    entries = store.load_entries()
    assert len(entries) == 200


# ── Multi-message content block handling ─────────────────────────────

def test_multiple_content_blocks_concatenated(tmp_path: Path) -> None:
    """Multiple text blocks are reconstructed individually (content_blocks_json preserves structure)."""
    msg = LLMMessage(role="user", content=[
        ContentBlock.text_block("part1"),
        ContentBlock.text_block("part2"),
        ContentBlock(type="json", text='{"key":"value"}'),
    ])
    store = ConversationStore(tmp_path)
    store.append(msg, node_id="n1")

    loaded = store.load_messages()
    assert len(loaded) == 1
    blocks = loaded[0].content
    assert len(blocks) == 3
    assert blocks[0].type == "text"
    assert blocks[0].text == "part1"
    assert blocks[1].type == "text"
    assert blocks[1].text == "part2"
    assert blocks[2].type == "json"
    assert blocks[2].text == '{"key":"value"}'

    # The legacy "content" field still concatenates text blocks
    entries = store.load_entries()
    assert "part1" in entries[0].content
    assert "part2" in entries[0].content


# ── Unicode / special characters ────────────────────────────────────

def test_unicode_round_trip(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    msg = LLMMessage.user("中文🎉 émoji — em dash • bullet")
    store.append(msg, node_id="n1")
    loaded = store.load_messages()
    assert loaded[0].content[0].text == "中文🎉 émoji — em dash • bullet"


def test_newlines_in_content_round_trip(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    text = "line 1\nline 2\n\nline 4"
    store.append(LLMMessage.user(text), node_id="n1")
    loaded = store.load_messages()
    assert loaded[0].content[0].text == text


# ── Corrupt line handling ────────────────────────────────────────────

def test_corrupt_lines_skipped(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    store.append(LLMMessage.user("good1"), node_id="n1")
    store.append(LLMMessage.user("good2"), node_id="n2")

    # Manually inject a corrupt line
    content = store.history_path.read_text(encoding="utf-8")
    corrupt = content + "this is not json\n"
    store.history_path.write_text(corrupt, encoding="utf-8")

    entries = store.load_entries()
    assert len(entries) == 2
    assert entries[0].content == "good1"
    assert entries[1].content == "good2"


def test_invalid_role_skipped(tmp_path: Path) -> None:
    """Entries with non-standard roles are skipped."""
    store = ConversationStore(tmp_path)
    store.append(LLMMessage.user("valid"), node_id="n1")
    # Inject line with bogus role
    bad = json.dumps({
        "id": "msg_bad", "role": "bogus_role", "content": "x",
        "timestamp": 1.0, "node_id": "n_bad",
    })
    store.history_path.write_text(
        store.history_path.read_text(encoding="utf-8") + bad + "\n",
        encoding="utf-8",
    )
    entries = store.load_entries()
    assert len(entries) == 1
    assert entries[0].content == "valid"


# ── Empty tool_calls serialization ──────────────────────────────────

def test_empty_tool_calls_preserved(tmp_path: Path) -> None:
    """Empty tool_calls list should not become None."""
    store = ConversationStore(tmp_path)
    store.append(LLMMessage.assistant("done"), node_id="n1", tool_calls=[], agent_id="a")
    entries = store.load_entries()
    assert entries[0].tool_calls == []
