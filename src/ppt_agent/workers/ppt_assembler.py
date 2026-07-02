from __future__ import annotations

from pathlib import Path

from ppt_agent.assembly.ppt_writer import write_pptx
from ppt_agent.coordinator.phase_state import load_artifact
from ppt_agent.models.artifacts import JobWorkspace


def run(workspace: JobWorkspace, force: bool = False) -> Path:
    output = workspace.root / "final.pptx"
    if output.exists() and not force:
        return output
    slide_contents = load_artifact(workspace, "slide_contents")
    if slide_contents.get("review_status") != "approved" and not force:
        raise ValueError("slide_contents.json must be approved before assembly; pass force=True for draft assembly")
    write_pptx(slide_contents, output)
    return output
