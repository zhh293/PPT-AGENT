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

    # Find the selected template's PPTX file
    template_path = _find_template_pptx(workspace)

    if template_path:
        logger.info("Using template: %s", template_path)
    else:
        logger.info("No template PPTX found, falling back to blank assembly")

    write_pptx(slide_contents, output, workspace_root=workspace.root, template_path=template_path)
    return output


def _find_template_pptx(workspace: JobWorkspace) -> Path | None:
    """Find the template PPTX file from the selected_template artifact."""
    try:
        selected = load_artifact(workspace, "selected_template")
    except (FileNotFoundError, Exception):
        return None

    template_path_str = selected.get("template_path", "")
    if not template_path_str:
        return None

    # template_path is relative to templates/ root
    tpl_pptx = Path("templates") / template_path_str / "template.pptx"
    if tpl_pptx.exists():
        return tpl_pptx

    # Also check if auto_ingest preserved the file
    alternate = Path("templates") / template_path_str / "template.pptx"
    # Try common locations
    for p in [tpl_pptx, alternate]:
        if p.exists():
            return p

    return None
