from __future__ import annotations

from pathlib import Path

from ppt_agent.coordinator.phase_state import load_artifact, write_artifact
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.models.design_plan import default_design_plan


def run(workspace: JobWorkspace, force: bool = False) -> Path:
    output = workspace.artifact_path("slide_design_plan")
    if output.exists() and not force:
        return output
    outline = load_artifact(workspace, "outline")
    return write_artifact(workspace, "slide_design_plan", default_design_plan(outline["meta"]["total_slides"]))
