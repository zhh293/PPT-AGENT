from __future__ import annotations

from pathlib import Path

from ppt_agent.coordinator.phase_state import load_artifact, write_artifact
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.models.outline import OutlineSlide, PresentationOutline


def run(workspace: JobWorkspace, force: bool = False) -> Path:
    output = workspace.artifact_path("outline")
    if output.exists() and not force:
        return output
    summary = load_artifact(workspace, "source_summary")
    capabilities = summary.get("product_capabilities", [])
    structure = [
        ("cover", summary["project_name"], "Introduce the project and intended message."),
        ("background", "背景与机会", "Explain why the project matters now."),
        ("problem", "核心问题", "Summarize the pain points or gaps."),
        ("solution", "解决方案", "Describe the proposed solution."),
        ("product", "产品能力", "Show the main capabilities."),
        ("evidence", "支撑材料", "Connect evidence from uploaded materials."),
        ("roadmap", "推进计划", "Outline next steps or implementation path."),
        ("closing", "总结与期待", "Close with value and call to action."),
    ]
    slides = []
    for index, (slide_type, title, purpose) in enumerate(structure):
        bullets = capabilities[index % len(capabilities) : index % len(capabilities) + 3] if capabilities else []
        if not bullets:
            bullets = [summary.get("value_proposition", "Review the provided project materials.")]
        slides.append(
            OutlineSlide(
                slide_index=index,
                type=slide_type,
                title=title,
                purpose=purpose,
                bullets=bullets[:3],
                source_refs=[item["evidence_id"] for item in summary.get("evidence_items", [])[:2]],
                image_needs="Use relevant user image or simple placeholder.",
                priority="required" if index in {0, 3, 7} else "recommended",
            )
        )
    outline = PresentationOutline(
        meta={
            "project_name": summary["project_name"],
            "domain": summary["domain"],
            "audience": summary["target_audience"],
            "tone": summary["tone"],
            "total_slides": len(slides),
            "assumptions": summary.get("warnings", []),
            "needs_user_review": True,
        },
        slides=slides,
    ).to_dict()
    return write_artifact(workspace, "outline", outline)
