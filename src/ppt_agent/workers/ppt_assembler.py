from __future__ import annotations

import logging
from pathlib import Path

from ppt_agent.assembly.ppt_writer import write_pptx
from ppt_agent.coordinator.phase_state import load_artifact
from ppt_agent.models.artifacts import JobWorkspace

logger = logging.getLogger(__name__)


def run(workspace: JobWorkspace, force: bool = False) -> Path:
    output = workspace.root / "final.pptx"
    if output.exists() and not force:
        return output
    slide_contents = load_artifact(workspace, "slide_contents")

    review_status = slide_contents.get("review_status")
    logger.info("slide_contents review_status: %s", review_status or "<not set>")

    if review_status != "approved" and not force:
        raise ValueError(
            "Cannot assemble PPT: slide_contents.json has not been approved by the user "
            f"(current review_status={review_status!r}). "
            "Please review slide_contents.json, set \"review_status\" to \"approved\", "
            "then re-run. Alternatively, pass force=True to assemble a draft without approval."
        )

    write_pptx(slide_contents, output, workspace_root=workspace.root)
    return output
