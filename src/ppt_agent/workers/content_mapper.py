"""Content mapping worker — map outline content into template zones.

Reads the outline, template zones, design plan, and source summary,
then produces slide_contents.json — the final zone-level content
mapping that drives both image generation and PPT assembly.

Key difference from the old approach: zone positions and types come
from the template's OCR/shape analysis, not hardcoded defaults.
Each slide's content is precisely mapped to the template's layout.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ppt_agent.coordinator.phase_state import load_artifact, write_artifact
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.models.slide_contents import SlideContent, SlideZoneContent, aggregate_review_status

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "content_mapping.md"


def _load_prompt() -> str:
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text(encoding="utf-8")
    return "Map outline content into slide zones based on template structure."


def _fallback_mapping(
    outline: dict,
    selected: dict,
    design: dict,
    source_summary: dict,
    template_zones: dict | None = None,
) -> dict:
    """Deterministic mapping — maps outline content to template zones.

    When template_zones is available (real template), uses the template's
    actual zone positions.  Otherwise falls back to hardcoded defaults.
    """
    image_inventory = source_summary.get("image_inventory", [])
    design_by_index = {item["slide_index"]: item for item in design.get("slides", [])}
    tpl_slides = {}
    if template_zones:
        tpl_slides = {s["index"]: s for s in template_zones.get("slides", [])}

    slides = []
    for slide in outline["slides"]:
        idx = slide["slide_index"]
        decision = design_by_index.get(idx, {})
        tpl_slide = tpl_slides.get(idx, {})
        image = image_inventory[idx % len(image_inventory)] if image_inventory else None

        # Build zones from template structure or defaults
        zones = _build_zones_for_slide(slide, tpl_slide, image, idx)

        # Get the template image path for this slide (for img2img)
        template_image = tpl_slide.get("template_image")

        slides.append(
            SlideContent(
                slide_index=idx,
                layout=decision.get("layout_id", tpl_slide.get("layout", "fallback.basic")),
                layout_id=decision.get("layout_id", tpl_slide.get("layout", "fallback.basic")),
                visual_density=decision.get("visual_density", "medium"),
                review_status="draft",
                zones=zones,
                source_refs=slide.get("source_refs", []),
                fallback_flags=[] if image else ["visual_placeholder"],
                template_image=template_image,
            ).to_dict()
        )

    return {
        "template_id": selected["template_id"],
        "review_status": aggregate_review_status(slides),
        "slides": slides,
    }


def _build_zones_for_slide(
    outline_slide: dict,
    tpl_slide: dict,
    image: dict | None,
    slide_index: int,
) -> list[SlideZoneContent]:
    """Build zone content list for a single slide.

    Uses template zones when available, falls back to default positions.
    """
    text_zones = tpl_slide.get("text_zones", [])
    image_zones = tpl_slide.get("image_zones", [])

    zones: list[SlideZoneContent] = []

    if text_zones:
        # Real template zones available — map content to them
        title_assigned = False
        body_assigned = False

        for tz in text_zones:
            zone_type = tz.get("type", "body")
            position = tz.get("position", [0.1, 0.1, 0.8, 0.8])

            if zone_type == "title" and not title_assigned:
                zones.append(SlideZoneContent(
                    tz.get("zone_id", f"title_{slide_index}"),
                    "title", position, True,
                    outline_slide["title"],
                    "generated", fit_status="fits",
                ))
                title_assigned = True
            elif zone_type in ("subtitle",) and not title_assigned:
                zones.append(SlideZoneContent(
                    tz.get("zone_id", f"subtitle_{slide_index}"),
                    "title", position, True,
                    outline_slide["title"],
                    "generated", fit_status="fits",
                ))
                title_assigned = True
            elif zone_type in ("body", "subtitle") and not body_assigned:
                zones.append(SlideZoneContent(
                    tz.get("zone_id", f"body_{slide_index}"),
                    "bullets", position, True,
                    outline_slide.get("bullets", []),
                    "generated", fit_status="fits",
                ))
                body_assigned = True
            elif zone_type == "footer":
                zones.append(SlideZoneContent(
                    tz.get("zone_id", f"footer_{slide_index}"),
                    "footer", position, True,
                    "",  # Footer text can be filled later
                    "generated", fit_status="fits",
                ))

        # Ensure we always have title and body even if template zones didn't match
        if not title_assigned:
            zones.insert(0, SlideZoneContent(
                "title", "title", [0.08, 0.08, 0.84, 0.16], True,
                outline_slide["title"], "generated", fit_status="fits",
            ))
        if not body_assigned:
            zones.append(SlideZoneContent(
                "body", "bullets", [0.10, 0.28, 0.56, 0.54], True,
                outline_slide.get("bullets", []), "generated", fit_status="fits",
            ))

    else:
        # No template zones — use defaults
        zones = [
            SlideZoneContent(
                "title", "title", [0.08, 0.08, 0.84, 0.16], True,
                outline_slide["title"], "generated", fit_status="fits",
            ),
            SlideZoneContent(
                "body", "bullets", [0.10, 0.28, 0.56, 0.54], True,
                outline_slide.get("bullets", []), "generated", fit_status="fits",
            ),
        ]

    # Add image zone
    if image_zones:
        for iz in image_zones:
            image_source = "user_upload" if image else "placeholder"
            image_ref = image.get("path") if image else None
            zones.append(SlideZoneContent(
                iz.get("zone_id", f"image_{slide_index}"),
                "image", iz.get("position", [0.70, 0.32, 0.22, 0.34]), False,
                None, image_source, image_ref=image_ref,
                image_prompt=outline_slide.get("image_needs"),
                fit_status="unknown",
            ))
    elif not any(z.zone_type == "image" for z in zones):
        # Add default image zone
        image_source = "user_upload" if image else "placeholder"
        image_ref = image.get("path") if image else None
        zones.append(SlideZoneContent(
            "visual", "image", [0.70, 0.32, 0.22, 0.34], False,
            None, image_source, image_ref=image_ref,
            image_prompt=outline_slide.get("image_needs"),
            fit_status="unknown",
        ))

    return zones


def _llm_mapping(llm_client, outline: dict, selected: dict, design: dict,
                 source_summary: dict, template_zones: dict | None = None) -> dict:
    """Use LLM for intelligent content mapping."""
    prompt = _load_prompt()

    image_inventory = source_summary.get("image_inventory", [])
    image_info = [
        {
            "image_id": img.get("image_id", ""),
            "path": img.get("path", ""),
            "detected_usage": img.get("detected_usage", ""),
            "summary": img.get("summary", ""),
        }
        for img in image_inventory
    ]

    context = {
        "outline": {
            "meta": outline.get("meta", {}),
            "slides": outline.get("slides", []),
        },
        "template_id": selected.get("template_id", "fallback.default"),
        "design_plan": {
            "theme_profile": design.get("theme_profile", {}),
            "slides": design.get("slides", []),
        },
        "image_inventory": image_info,
    }

    # Include template zone structure if available
    if template_zones:
        context["template_zones"] = {
            "color_scheme": template_zones.get("color_scheme", ""),
            "slides": [
                {
                    "index": s["index"],
                    "text_zones": s.get("text_zones", []),
                    "image_zones": s.get("image_zones", []),
                    "layout": s.get("layout", ""),
                    "has_template_image": s.get("template_image") is not None,
                }
                for s in template_zones.get("slides", [])
            ],
        }

    fallback = _fallback_mapping(outline, selected, design, source_summary, template_zones)

    result = llm_client.generate_json(
        prompt=prompt,
        context=context,
        system="You are a content editor for PPT generation. Output only valid JSON.",
        phase="content_mapping",
        fallback=fallback,
    )

    _ensure_valid_mapping(result, outline, selected, design, source_summary, template_zones)
    return result


def _ensure_valid_mapping(
    mapping: dict, outline: dict, selected: dict, design: dict,
    source_summary: dict, template_zones: dict | None = None,
) -> None:
    """Ensure the LLM mapping has all required fields."""
    mapping.setdefault("template_id", selected.get("template_id", "fallback.default"))
    mapping.setdefault("review_status", "draft")

    if "slides" not in mapping or not mapping["slides"]:
        fallback = _fallback_mapping(outline, selected, design, source_summary, template_zones)
        mapping["slides"] = fallback["slides"]
        return

    design_by_index = {item["slide_index"]: item for item in design.get("slides", [])}
    image_inventory = source_summary.get("image_inventory", [])
    tpl_slides = {}
    if template_zones:
        tpl_slides = {s["index"]: s for s in template_zones.get("slides", [])}

    for i, slide in enumerate(mapping["slides"]):
        slide["slide_index"] = i
        decision = design_by_index.get(i, {})
        tpl_slide = tpl_slides.get(i, {})
        slide.setdefault("layout", decision.get("layout_id", tpl_slide.get("layout", "fallback.basic")))
        slide.setdefault("layout_id", slide["layout"])
        slide.setdefault("visual_density", decision.get("visual_density", "medium"))
        slide.setdefault("review_status", "draft")
        slide.setdefault("source_refs", [])
        slide.setdefault("fallback_flags", [])

        # Carry template_image through for downstream img2img
        if tpl_slide.get("template_image"):
            slide.setdefault("template_image", tpl_slide["template_image"])

        if "zones" not in slide or not slide["zones"]:
            outline_slide = outline["slides"][i] if i < len(outline["slides"]) else {}
            image = image_inventory[i % len(image_inventory)] if image_inventory else None
            tpl_text_zones = tpl_slide.get("text_zones", [])

            if tpl_text_zones:
                # Use template zone positions
                slide["zones"] = [z.to_dict() if hasattr(z, 'to_dict') else z
                                  for z in _build_zones_for_slide(outline_slide, tpl_slide, image, i)]
            else:
                slide["zones"] = [
                    {"zone_id": "title", "type": "title", "position": [0.08, 0.08, 0.84, 0.16],
                     "editable": True, "content": outline_slide.get("title", f"Slide {i+1}"),
                     "source": "generated", "fit_status": "fits"},
                    {"zone_id": "body", "type": "bullets", "position": [0.10, 0.28, 0.56, 0.54],
                     "editable": True, "content": outline_slide.get("bullets", []),
                     "source": "generated", "fit_status": "fits"},
                    {"zone_id": "visual", "type": "image", "position": [0.70, 0.32, 0.22, 0.34],
                     "editable": False, "source": "user_upload" if image else "placeholder",
                     "image_ref": image.get("path") if image else None, "fit_status": "unknown"},
                ]
        else:
            for zone in slide["zones"]:
                zone.setdefault("zone_id", "unknown")
                zone.setdefault("type", "text")
                zone.setdefault("position", [0.1, 0.1, 0.8, 0.8])
                zone.setdefault("editable", zone["type"] in ("title", "subtitle", "bullets"))
                zone.setdefault("source", "generated")
                zone.setdefault("fit_status", "unknown")

    mapping["review_status"] = aggregate_review_status(mapping["slides"])


def run(workspace: JobWorkspace, force: bool = False, llm_client=None) -> Path:
    output = workspace.artifact_path("slide_contents")
    if output.exists() and not force:
        return output

    outline = load_artifact(workspace, "outline")
    selected = load_artifact(workspace, "selected_template")
    design = load_artifact(workspace, "slide_design_plan")
    source_summary = load_artifact(workspace, "source_summary")

    # Load template zones if available
    template_zones = None
    try:
        template_zones = load_artifact(workspace, "template_zones")
    except (FileNotFoundError, Exception):
        logger.info("No template_zones artifact — using default zone positions")

    if llm_client is not None:
        logger.info("Using LLM for content mapping")
        payload = _llm_mapping(llm_client, outline, selected, design, source_summary, template_zones)
    else:
        logger.info("No LLM client, using fallback content mapping")
        payload = _fallback_mapping(outline, selected, design, source_summary, template_zones)

    return write_artifact(workspace, "slide_contents", payload)
