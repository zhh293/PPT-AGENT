from __future__ import annotations

import json
import logging
from pathlib import Path

from ppt_agent.coordinator.phase_state import load_artifact, write_artifact
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.models.outline import OutlineSlide, PresentationOutline

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "outline_generation.md"


def _load_prompt() -> str:
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text(encoding="utf-8")
    return "Generate a PPT outline based on the following project analysis."


def _fallback_outline(summary: dict) -> dict:
    """Original hardcoded outline — used when no LLM is available."""
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
    return PresentationOutline(
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


def _llm_outline(llm_client, summary: dict) -> dict:
    """Use LLM to generate an intelligent outline."""
    prompt = _load_prompt()

    context = {
        "project_name": summary.get("project_name", ""),
        "domain": summary.get("domain", "general"),
        "target_audience": summary.get("target_audience", "business stakeholders"),
        "tone": summary.get("tone", "professional"),
        "value_proposition": summary.get("value_proposition", ""),
        "product_capabilities": summary.get("product_capabilities", []),
        "evidence_items": summary.get("evidence_items", []),
        "core_pain_points": summary.get("core_pain_points", []),
        "image_inventory": [
            {"image_id": img.get("image_id", ""), "detected_usage": img.get("detected_usage", ""), "summary": img.get("summary", "")}
            for img in summary.get("image_inventory", [])
        ],
        "warnings": summary.get("warnings", []),
    }

    fallback = _fallback_outline(summary)

    result = llm_client.generate_json(
        prompt=prompt,
        context=context,
        system="You are a PPT outline architect. Output only valid JSON.",
        phase="outline_generation",
        fallback=fallback,
    )

    # Validate and fix the result
    _ensure_valid_outline(result, summary)
    return result


def _ensure_valid_outline(outline: dict, summary: dict) -> None:
    """Ensure the LLM-generated outline has all required fields."""
    # Ensure meta exists
    if "meta" not in outline:
        outline["meta"] = {}

    meta = outline["meta"]
    meta.setdefault("project_name", summary.get("project_name", "Untitled"))
    meta.setdefault("domain", summary.get("domain", "general"))
    meta.setdefault("audience", summary.get("target_audience", "business stakeholders"))
    meta.setdefault("tone", summary.get("tone", "professional"))
    meta.setdefault("needs_user_review", True)
    meta.setdefault("assumptions", [])

    # Ensure slides exist
    if "slides" not in outline or not outline["slides"]:
        outline["slides"] = _fallback_outline(summary)["slides"]
        return

    # Fix slide indexes and required fields
    for i, slide in enumerate(outline["slides"]):
        slide["slide_index"] = i
        slide.setdefault("type", "content")
        slide.setdefault("title", f"Slide {i+1}")
        slide.setdefault("purpose", "")
        slide.setdefault("bullets", [])
        slide.setdefault("source_refs", [])
        slide.setdefault("image_needs", "concept illustration")
        slide.setdefault("priority", "recommended")

    meta["total_slides"] = len(outline["slides"])


def run(workspace: JobWorkspace, force: bool = False, llm_client=None) -> Path:
    output = workspace.artifact_path("outline")
    if output.exists() and not force:
        return output

    summary = load_artifact(workspace, "source_summary")

    if llm_client is not None:
        logger.info("Using LLM for outline generation")
        outline = _llm_outline(llm_client, summary)
    else:
        logger.info("No LLM client, using fallback outline")
        outline = _fallback_outline(summary)

    return write_artifact(workspace, "outline", outline)
