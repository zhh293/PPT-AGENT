from __future__ import annotations

from pathlib import Path

from ppt_agent.coordinator.phase_state import load_artifact, write_artifact
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.models.slide_contents import SlideContent, SlideZoneContent, aggregate_review_status


def run(workspace: JobWorkspace, force: bool = False) -> Path:
    output = workspace.artifact_path("slide_contents")
    if output.exists() and not force:
        return output
    outline = load_artifact(workspace, "outline")
    selected = load_artifact(workspace, "selected_template")
    design = load_artifact(workspace, "slide_design_plan")
    source_summary = load_artifact(workspace, "source_summary")
    image_inventory = source_summary.get("image_inventory", [])
    design_by_index = {item["slide_index"]: item for item in design.get("slides", [])}
    slides = []
    for slide in outline["slides"]:
        decision = design_by_index.get(slide["slide_index"], {})
        image = image_inventory[slide["slide_index"] % len(image_inventory)] if image_inventory else None
        image_source = "user_upload" if image else "placeholder"
        image_ref = image.get("path") if image else None
        fallback_flags = [] if image else ["visual_placeholder"]
        zones = [
            SlideZoneContent("title", "title", [0.08, 0.08, 0.84, 0.16], True, slide["title"], "generated", fit_status="fits"),
            SlideZoneContent("bullets", "bullets", [0.10, 0.28, 0.56, 0.54], True, slide.get("bullets", []), "generated", fit_status="fits"),
            SlideZoneContent("visual", "image", [0.70, 0.32, 0.22, 0.34], False, None, image_source, image_ref=image_ref, image_prompt=slide.get("image_needs"), fit_status="unknown"),
        ]
        slides.append(
            SlideContent(
                slide_index=slide["slide_index"],
                layout=decision.get("layout_id", "fallback.basic"),
                layout_id=decision.get("layout_id", "fallback.basic"),
                visual_density=decision.get("visual_density", "medium"),
                review_status="draft",
                zones=zones,
                source_refs=slide.get("source_refs", []),
                fallback_flags=fallback_flags,
            ).to_dict()
        )
    payload = {"template_id": selected["template_id"], "review_status": aggregate_review_status(slides), "slides": slides}
    return write_artifact(workspace, "slide_contents", payload)
