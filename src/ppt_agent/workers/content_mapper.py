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


def _estimate_capacity(zone: dict) -> str:
    """Return a human-readable capacity hint for a template zone."""
    pos = zone.get("position", [0, 0, 0, 0])
    w, h = pos[2] if len(pos) > 2 else 0.5, pos[3] if len(pos) > 3 else 0.5
    area = w * h
    fmt = zone.get("formatting", {}) or {}
    font_size = fmt.get("font_size_pt", 14) or 14
    chars_per_line = max(5, int(w * 10 * (10 / max(int(font_size), 10))))
    max_lines = max(1, int(h * 720 / max(int(font_size), 10)))

    if area > 0.3:
        return (
            f"large area ({area:.2f}), {font_size}pt, "
            f"~{chars_per_line} chars/line, ~{max_lines} lines — good for title, key number, or 3+ bullets"
        )
    elif area > 0.1:
        return (
            f"medium area ({area:.2f}), {font_size}pt, "
            f"~{chars_per_line} chars/line, ~{max_lines} lines — good for body text or 2-3 bullets"
        )
    else:
        return (
            f"small area ({area:.2f}), {font_size}pt, "
            f"~{chars_per_line} chars/line, ~{max_lines} lines — good for footer, label, or short text"
        )


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
    """Build zone content list for a single slide (deterministic fallback).

    Uses simple type-to-type matching: title→title zone, bullets→body zone.
    This is the FALLBACK path.  The LLM path does direct zone assignment.
    """
    text_zones = tpl_slide.get("text_zones", [])
    image_zones = tpl_slide.get("image_zones", [])

    zones: list[SlideZoneContent] = []

    if text_zones:
        title_assigned = False
        body_assigned = False

        # Sort: prefer zones WITH formatting data first
        def _zone_sort_key(tz):
            fmt = tz.get("formatting", {})
            has_fmt = 1 if (fmt.get("font_size_pt") or fmt.get("font_name") or fmt.get("font_color")) else 0
            # Preference: title/subtitle first, then body/footer. Same type: formatting wins
            type_order = {"title": 0, "subtitle": 1, "body": 2, "footer": 3}
            return (has_fmt * -1, type_order.get(tz.get("type", "body"), 99))  # -has_fmt puts formatted first

        sorted_zones = sorted(text_zones, key=_zone_sort_key)

        for tz in sorted_zones:
            zone_type = tz.get("type", "body")
            position = tz.get("position", [0.1, 0.1, 0.8, 0.8])
            formatting = tz.get("formatting")

            if zone_type == "title" and not title_assigned:
                zones.append(SlideZoneContent(
                    tz.get("zone_id", f"title_{slide_index}"),
                    "title", position, True,
                    outline_slide["title"], "generated", fit_status="fits",
                    formatting=formatting,
                ))
                title_assigned = True
            elif zone_type in ("subtitle",) and not title_assigned:
                zones.append(SlideZoneContent(
                    tz.get("zone_id", f"subtitle_{slide_index}"),
                    "title", position, True,
                    outline_slide["title"], "generated", fit_status="fits",
                    formatting=formatting,
                ))
                title_assigned = True
            elif zone_type in ("body", "subtitle") and not body_assigned:
                zones.append(SlideZoneContent(
                    tz.get("zone_id", f"body_{slide_index}"),
                    "bullets", position, True,
                    outline_slide.get("bullets", []), "generated", fit_status="fits",
                    formatting=formatting,
                ))
                body_assigned = True
            elif zone_type == "footer":
                zones.append(SlideZoneContent(
                    tz.get("zone_id", f"footer_{slide_index}"),
                    "footer", position, True,
                    "", "generated", fit_status="fits",
                    formatting=formatting,
                ))

        if not title_assigned:
            tid = _find_fallback_zone_id("title", tpl_slide) or "title"
            zones.insert(0, SlideZoneContent(
                tid, "title", [0.08, 0.08, 0.84, 0.16], True,
                outline_slide["title"], "generated", fit_status="fits",
            ))
        if not body_assigned:
            bid = _find_fallback_zone_id("body", tpl_slide) or "body"
            zones.append(SlideZoneContent(
                bid, "bullets", [0.10, 0.28, 0.56, 0.54], True,
                outline_slide.get("bullets", []), "generated", fit_status="fits",
            ))
    else:
        tid = _find_fallback_zone_id("title", tpl_slide) or "title"
        bid = _find_fallback_zone_id("body", tpl_slide) or "body"
        zones = [
            SlideZoneContent(
                tid, "title", [0.08, 0.08, 0.84, 0.16], True,
                outline_slide["title"], "generated", fit_status="fits",
            ),
            SlideZoneContent(
                bid, "bullets", [0.10, 0.28, 0.56, 0.54], True,
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
                formatting=iz.get("formatting"),
            ))
    elif not any(z.zone_type == "image" for z in zones):
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
                    "all_zones": [
                        {
                            "zone_id": z.get("zone_id", ""),
                            "type": z.get("type", "body"),
                            "position": z.get("position", [0, 0, 0, 0]),
                            "formatting": z.get("formatting", {}),
                            "visual": z.get("visual", {}),
                        }
                        for z in s.get("all_zones", [])
                    ],
                    "layout": s.get("layout", ""),
                    "has_template_image": s.get("template_image") is not None,
                }
                for s in template_zones.get("slides", [])
            ],
        }

        # ── Add capacity hints to each zone ──
        for s in context["template_zones"]["slides"]:
            for z in s.get("all_zones", []):
                z["capacity_hint"] = _estimate_capacity(z)

    # ── AI Direct Zone Assignment prompt ──
    assignment_instruction = """
## Direct Zone Assignment

You have ALL the information needed to make layout decisions. The template
zones include their exact position, formatting (font/size/alignment), visual
characteristics, and capacity hints.

### Your job
For each slide, decide WHICH content goes into WHICH zone. You may:
- Split bullets across multiple zones if some deserve visual emphasis
- Put key statistics in larger/highlighted zones
- Leave decorative zones empty (content: null)
- Group related content together in the most suitable zone

### Critical rules
1. **zone_id**: COPY the exact zone_id from the template all_zones. Do NOT invent IDs.
2. **position**: COPY the position array from the template zone. Do NOT calculate or guess.
3. **formatting**: Preserve any existing formatting from the template zone.
4. **placement_reason**: For each zone with content, add a brief explanation of why
   you chose that zone for that content. This helps review and debugging.
5. **image zones**: Keep them with content: null and the original image_prompt.
6. **capacity**: Read the capacity_hint on each zone — don't put 10 bullets in a
   zone that only fits 2-3 lines.
7. Output ONLY the JSON object — no markdown, no explanation outside the JSON.
"""

    enhanced_prompt = (prompt + "\n\n" + assignment_instruction).strip()

    fallback = _fallback_mapping(outline, selected, design, source_summary, template_zones)

    result = llm_client.generate_json(
        prompt=enhanced_prompt,
        context=context,
        system="You are a content editor for PPT generation. Output only valid JSON.",
        phase="content_mapping",
        fallback=fallback,
        max_tokens=16000,
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

        # ── Validate zone_ids (must exist in template all_zones) ──
        outline_slide = outline["slides"][i] if i < len(outline["slides"]) else {}
        if tpl_slide:
            valid_zone_ids = {z.get("zone_id", "") for z in tpl_slide.get("all_zones", [])}
            if valid_zone_ids:
                for zone in slide.get("zones", []):
                    zid = zone.get("zone_id", "")
                    ztype = zone.get("type", "text")
                    if zid not in valid_zone_ids and ztype not in ("image", "chart", "shape"):
                        # Try to find a fallback zone by type
                        fallback_zid = _find_fallback_zone_id(ztype, tpl_slide)
                        if fallback_zid:
                            zone["zone_id"] = fallback_zid
                            zone.setdefault("_warning", f"zone_id not in template, auto-assigned to {fallback_zid}")
                        else:
                            zone.setdefault("_warning", f"zone_id {zid} not in template and no fallback found")

        # ── Force template zone positions (LLM may invent wrong ones) ──
        if tpl_slide:
            tpl_text_zones = tpl_slide.get("text_zones", [])
            tpl_image_zones = tpl_slide.get("image_zones", [])
            if tpl_text_zones or tpl_image_zones:
                slide["zones"] = _lock_zone_positions(
                    slide.get("zones", []),
                    tpl_text_zones,
                    tpl_image_zones,
                )

        # ── Preserve template formatting on matched zones ──
        if tpl_slide:
            slide["zones"] = _inject_formatting(slide.get("zones", []), tpl_slide)

        # Carry template_image through for downstream img2img
        if tpl_slide.get("template_image"):
            slide.setdefault("template_image", tpl_slide["template_image"])

        if "zones" not in slide or not slide["zones"]:
            image = image_inventory[i % len(image_inventory)] if image_inventory else None

            if tpl_slide.get("text_zones"):
                # Use template zone positions with semantic label matching
                slide["zones"] = [z.to_dict() if hasattr(z, 'to_dict') else z
                                  for z in _build_zones_for_slide(outline_slide, tpl_slide, image, i)]
            else:
                slide["zones"] = [
                    {"zone_id": "title", "type": "title", "position": [0.08, 0.08, 0.84, 0.16],
                     "editable": True, "content": outline_slide.get("title", f"Slide {i+1}"),
                     "source": "generated", "fit_status": "fits", "semantic_label": "页面标题"},
                    {"zone_id": "body", "type": "bullets", "position": [0.10, 0.28, 0.56, 0.54],
                     "editable": True, "content": outline_slide.get("bullets", []),
                     "source": "generated", "fit_status": "fits", "semantic_label": "支撑论据"},
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
                zone.setdefault("semantic_label", "")
                if zone.get("formatting") is None:
                    zone["formatting"] = None

    mapping["review_status"] = aggregate_review_status(mapping["slides"])


def _lock_zone_positions(
    llm_zones: list[dict],
    tpl_text_zones: list[dict],
    tpl_image_zones: list[dict],
) -> list[dict]:
    """Force zone positions to match template zones.

    The LLM decides which content goes to which zone, but the POSITION
    must come from the template's actual shape coordinates, not the LLM's
    invented layout.

    Matches LLM zones to template zones by type (title→title, body→body, etc.)
    using a greedy best-match approach.
    """
    # Build pools of template zones grouped by type
    tpl_by_type: dict[str, list[dict]] = {}
    for tz in tpl_text_zones:
        t = tz.get("type", "body")
        if t not in tpl_by_type:
            tpl_by_type[t] = []
        tpl_by_type[t].append(tz)
    for iz in tpl_image_zones:
        if "image" not in tpl_by_type:
            tpl_by_type["image"] = []
        tpl_by_type["image"].append(iz)

    # Track which template zones have been used
    used: set[int] = set()  # by id(tz)

    for zone in llm_zones:
        zone_type = zone.get("type", "body")
        candidates = tpl_by_type.get(zone_type, [])

        # Find best unused template zone of matching type.
        # If no exact match, fall back: subtitle→body, bullets→body, body→title.
        _FALLBACK_TYPES = {
            "subtitle": ["body", "title"],
            "bullets": ["body", "subtitle"],
            "body": ["title", "subtitle"],
        }
        type_options = [zone_type] + _FALLBACK_TYPES.get(zone_type, [])
        available = []
        for t in type_options:
            pool = tpl_by_type.get(t, [])
            available = sorted(
                [tz for tz in pool if id(tz) not in used],
                key=lambda tz: (
                    0 if tz["position"][0] < 0.85 else 1,
                    tz["position"][1],
                    -(tz["position"][2]),
                ),
            )
            if available:
                break

        if available:
            # Use the first available template zone of matching type
            tpl_zone = available[0]
            zone["position"] = tpl_zone["position"]
            zone["zone_id"] = tpl_zone.get("zone_id", zone.get("zone_id", ""))
            used.add(id(tpl_zone))

    return llm_zones


def _inject_formatting(zones: list[dict], tpl_slide: dict) -> list[dict]:
    """Inject template formatting data into matched zones.

    For each zone, find the matching template zone by zone_id and copy
    its ``formatting`` dict (font_name, font_size_pt, font_color, alignment).
    If no match, leaves existing formatting untouched.
    """
    all_tpl_zones = tpl_slide.get("all_zones", tpl_slide.get("text_zones", []))
    tpl_by_id: dict[str, dict] = {z.get("zone_id", ""): z for z in all_tpl_zones}

    for zone in zones:
        zid = zone.get("zone_id", "")
        if not zid or zone.get("type") not in ("title", "subtitle", "bullets", "body", "footer"):
            continue

        tpl_zone = tpl_by_id.get(zid)
        if tpl_zone and tpl_zone.get("formatting"):
            zone["formatting"] = tpl_zone["formatting"]

        # Inject visual hints as well
        if tpl_zone and tpl_zone.get("visual"):
            zone.setdefault("visual", tpl_zone["visual"])

    return zones


def _find_fallback_zone_id(zone_type: str, tpl_slide: dict) -> str:
    """Find a matching template zone_id by type for when LLM invents one."""
    zone_type_map = {
        "title": ["title", "subtitle"],
        "subtitle": ["subtitle", "title"],
        "bullets": ["body", "subtitle"],
        "body": ["body", "title"],
        "footer": ["footer", "body"],
    }
    preferred = zone_type_map.get(zone_type, [zone_type])

    for z in tpl_slide.get("all_zones", tpl_slide.get("text_zones", [])):
        if z.get("type") in preferred:
            return z.get("zone_id", "")

    # Last resort: any text-looking zone
    for z in tpl_slide.get("all_zones", []):
        visual = z.get("visual", {})
        if visual.get("is_content_area") or visual.get("text_likeness", 0) > 0.6:
            return z.get("zone_id", "")

    return ""


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
