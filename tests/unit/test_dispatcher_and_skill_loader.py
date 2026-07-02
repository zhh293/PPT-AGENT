from __future__ import annotations

from pathlib import Path

from ppt_agent.coordinator.dispatcher import dispatch
from ppt_agent.models.dispatch import DispatchRequest
from ppt_agent.skills.loader import load_skill


def test_dispatch_and_skill_loading(tmp_path: Path) -> None:
    decision = dispatch(tmp_path, DispatchRequest("job", "outline_generation", "outline_generation", []))
    assert decision.skill == "ppt-outline-generator"
    assert "outline" in load_skill("ppt-outline-generator").lower()
