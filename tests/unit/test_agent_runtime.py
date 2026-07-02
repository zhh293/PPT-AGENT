from __future__ import annotations

from pathlib import Path

import pytest

from ppt_agent.context.dream import append_relevant_memory
from ppt_agent.runtime.agent_context import agent_attribution, current_agent
from ppt_agent.runtime.coordinator_tools import assert_allowed_tool
from ppt_agent.runtime.mailbox import Mailbox
from ppt_agent.runtime.task_types import make_task_id


def test_runtime_boundaries(tmp_path: Path) -> None:
    assert make_task_id("worker", 1) == "work-0001"
    with pytest.raises(PermissionError):
        assert_allowed_tool("FilesystemTool")
    box = Mailbox(tmp_path / "mailbox.jsonl")
    box.append("a", "b", "hello", ["artifact.json"])
    assert box.read_all()[0]["artifact_refs"] == ["artifact.json"]
    with agent_attribution("worker"):
        assert current_agent.get() == "worker"
    assert append_relevant_memory(tmp_path, "remember", 0.9)
    assert not append_relevant_memory(tmp_path, "ignore", 0.1)
