from __future__ import annotations

import logging
from pathlib import Path

from ppt_agent.coordinator.phase_state import load_artifact, write_artifact
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.models.design_plan import default_design_plan

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "design_planning.md"


def _load_prompt() -> str:
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text(encoding="utf-8")
    return "Create a design plan for the following presentation outline."


def _llm_design(llm_client, outline: dict, template_meta: dict | None) -> dict:
    """Use LLM to generate an intelligent design plan."""
    prompt = _load_prompt()

    slides_summary = []
    for slide in outline.get("slides", []):
        slides_summary.append({
            "slide_index": slide.get("slide_index"),
            "type": slide.get("type"),
            "title": slide.get("title"),
            "bullet_count": len(slide.get("bullets", [])),
            "image_needs": slide.get("image_needs", ""),
        })

    context = {
        "project_name": outline.get("meta", {}).get("project_name", ""),
        "domain": outline.get("meta", {}).get("domain", "general"),
        "audience": outline.get("meta", {}).get("audience", "business stakeholders"),
        "tone": outline.get("meta", {}).get("tone", "professional"),
        "total_slides": outline.get("meta", {}).get("total_slides", 8),
        "slides": slides_summary,
    }

    if template_meta:
        context["template"] = {
            "template_id": template_meta.get("template_id", ""),
            "color_scheme": template_meta.get("color_scheme", {}),
        }

    slide_count = outline.get("meta", {}).get("total_slides", 8)
    fallback = default_design_plan(slide_count)

    from ppt_agent.llm.schemas import DESIGN_PLAN_SCHEMA

    result = llm_client.generate_json(
        prompt=prompt,
        context=context,
        system="You are a presentation design director. Output only valid JSON.",
        phase="design_planning",
        fallback=fallback,
        json_schema=DESIGN_PLAN_SCHEMA,
        schema_name="slide_design_plan",
    )

    # Validate and fix
    _ensure_valid_design(result, slide_count)
    return result


def _ensure_valid_design(plan: dict, slide_count: int) -> None:
    """Ensure the design plan has all required fields."""
    # Ensure theme_profile
    if "theme_profile" not in plan:
        plan["theme_profile"] = default_design_plan(1)["theme_profile"]

    theme = plan["theme_profile"]
    theme.setdefault("theme_id", "generated.professional")
    theme.setdefault("color_tokens", {})
    theme["color_tokens"].setdefault("primary", "#1F4E79")
    theme["color_tokens"].setdefault("background", "#FFFFFF")
    theme["color_tokens"].setdefault("text", "#1F2933")
    theme.setdefault("typography_tokens", {"title_font": "Aptos Display", "body_font": "Aptos", "title_scale": 1.0, "body_scale": 1.0})
    theme.setdefault("spacing_tokens", {"page_margin": 0.08, "block_gap": 0.03, "card_padding": 0.02})
    theme.setdefault("shape_tokens", {"border_radius": 0.02, "stroke": "light"})
    theme.setdefault("image_treatment", {"crop": "contain", "tone": "natural"})
    theme.setdefault("chart_style", {"palette": ["#1F4E79", "#70AD47", "#F4B183"]})

    # Ensure slides
    if "slides" not in plan or not plan["slides"]:
        plan["slides"] = default_design_plan(slide_count)["slides"]
    else:
        for i, slide in enumerate(plan["slides"]):
            slide["slide_index"] = i
            slide.setdefault("layout_id", "cover.hero" if i == 0 else "fallback.basic")
            slide.setdefault("visual_density", "low" if i in {0, slide_count - 1} else "medium")
            slide.setdefault("block_plan", [{"block_type": "editable_text", "purpose": "communicate slide message", "priority": "primary"}])
            slide.setdefault("visual_strategy", "placeholder")
            slide.setdefault("text_budget", {"max_title_chars": 70, "max_bullets": 5, "max_lines_per_block": 8})
            slide.setdefault("design_constraints", ["preserve approved text as editable PPT text"])
            slide.setdefault("design_warnings", [])

    plan.setdefault("global_style_notes", [])
    plan.setdefault("design_risks", [])


def run(workspace: JobWorkspace, force: bool = False, llm_client=None) -> Path:
    output = workspace.artifact_path("slide_design_plan")
    if output.exists() and not force:
        return output

    outline = load_artifact(workspace, "outline")

    # Try to load template_meta if available
    template_meta = None
    try:
        template_meta = load_artifact(workspace, "template_meta")
    except (FileNotFoundError, Exception):
        pass

    if llm_client is not None:
        logger.info("Using LLM for design planning")
        plan = _llm_design(llm_client, outline, template_meta)
    else:
        logger.info("No LLM client, using fallback design plan")
        slide_count = outline["meta"]["total_slides"]
        plan = default_design_plan(slide_count)

    return write_artifact(workspace, "slide_design_plan", plan)
