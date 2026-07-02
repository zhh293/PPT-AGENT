from __future__ import annotations

from pathlib import Path

from ppt_agent.coordinator.phase_state import load_artifact, write_artifact
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.models.validation import validation_report


def run(workspace: JobWorkspace, force: bool = False) -> Path:
    output = workspace.artifact_path("validation_report")
    if output.exists() and not force:
        return output
    slide_contents = load_artifact(workspace, "slide_contents")
    warnings = []
    image_report_path = workspace.artifact_path("image_generation_report")
    if image_report_path.exists():
        image_report = load_artifact(workspace, "image_generation_report")
        warnings.extend(image_report.get("warnings", []))
    if not (workspace.root / "final.pptx").exists():
        warnings.append("final.pptx is missing")
    return write_artifact(workspace, "validation_report", validation_report(workspace.root.name, slide_contents, warnings))
