from __future__ import annotations

from pathlib import Path

from ppt_agent.coordinator.phase_state import load_artifact, write_artifact
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.models.image_generation import fallback_image_report
from ppt_agent.skills.adapters.gptimage2 import slide_contents_to_batch_config


def run(workspace: JobWorkspace, force: bool = False) -> Path:
    output = workspace.artifact_path("image_generation_report")
    if output.exists() and not force:
        return output
    slide_contents = load_artifact(workspace, "slide_contents")
    write_artifact(workspace, "image_generation_config", slide_contents_to_batch_config(slide_contents))
    return write_artifact(workspace, "image_generation_report", fallback_image_report(workspace.root.name, len(slide_contents.get("slides", []))))
