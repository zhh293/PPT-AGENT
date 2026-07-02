from __future__ import annotations

from pathlib import Path

import pytest

from ppt_agent.tools.filesystem import SafeFilesystem
from ppt_agent.tools.permissions import assert_tool_allowed
from ppt_agent.tools.registry import ToolDescriptor


def test_tool_permissions_and_sandbox(tmp_path: Path) -> None:
    descriptor = ToolDescriptor("fs", "filesystem", {"worker"})
    assert_tool_allowed(descriptor, "worker", "filesystem")
    with pytest.raises(PermissionError):
        assert_tool_allowed(descriptor, "coordinator")
    fs = SafeFilesystem(tmp_path)
    fs.atomic_write_text("a/b.txt", "ok")
    assert (tmp_path / "a/b.txt").read_text(encoding="utf-8") == "ok"
    with pytest.raises(PermissionError):
        fs.resolve(tmp_path.parent / "escape.txt")
