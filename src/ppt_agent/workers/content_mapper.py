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
import os
import re
from difflib import SequenceMatcher
from pathlib import Path

from ppt_agent.coordinator.phase_state import load_artifact, write_artifact
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.models.slide_contents import SlideContent, SlideZoneContent, aggregate_review_status

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "content_mapping.md"


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_optional_nonnegative_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("Ignoring invalid integer environment value %s=%r", name, raw)
        return None

_ZONE_REPAIR_SCHEMA: dict = {
    "title": "zone_semantic_repairs",
    "type": "object",
    "properties": {
        "repairs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "zone_id": {"type": "string"},
                    "content": {
                        "anyOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ]
                    },
                    "placement_reason": {"type": "string"},
                    "source_block_ids": {
                        "type": "array", "items": {"type": "string"}
                    },
                    "transformation": {"type": "string"},
                },
                "required": [
                    "zone_id", "content", "placement_reason",
                    "source_block_ids", "transformation",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["repairs"],
    "additionalProperties": False,
}


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
    chars_per_line = max(5, int(w * 650 / max(int(font_size), 10)))
    max_lines = max(1, int(h * 900 / max(int(font_size), 10)))

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


def _normalized_text(value: str) -> str:
    return "".join(re.findall(r"[\w\u3400-\u9fff]+", value.lower()))


def _clean_mapping_text(value: object) -> str:
    """Remove source-format and internal planning markers from visible copy."""
    text = str(value or "").strip()
    text = re.sub(
        r"(?<![A-Za-z0-9])\.?(?:md|docx|txt)(?![A-Za-z0-9])\s*[:：;；,，]*",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"(?:^|[\[;,，；])\s*\.?(?:md|docx|txt)\s*[:：;；,，\]\-]*",
        " ",
        text,
        flags=re.I,
    )
    text = re.sub(r"^\s*[-*#]+\s*", "", text)
    text = re.sub(r"^\s*\.?(?:md|docx|txt)\s*[;；,:：-]*\s*", "", text, flags=re.I)
    text = re.sub(r"\[(?:project[_ -]?plan|source|document|[^\]]+\.(?:md|docx|txt))\]?", "", text, flags=re.I)
    text = text.replace("project_plan", "").replace("project plan", "")
    text = re.sub(r"\s+", " ", text).strip(" -_[]：:；;")
    return text


def _content_text(content: object) -> str:
    if isinstance(content, list):
        return "\n".join(str(item) for item in content if str(item).strip())
    return str(content or "")


def _visible_char_count(value: object) -> int:
    return len(re.sub(r"\s+", "", _content_text(value)))


def _zone_target_range(zone: dict) -> tuple[int, int]:
    """Return an original-copy-aware target range without changing formatting."""
    original_length = _visible_char_count(
        zone.get("original_text", zone.get("text", ""))
    )
    eligibility = zone.get("content_eligibility") or zone.get("type", "body")
    hinted_max = int(zone.get("max_chars_hint") or 0)
    position = zone.get("position", [0, 0, 0, 0])
    formatting = zone.get("formatting", {}) or {}
    font_pt = float(formatting.get("font_size_pt") or 14)
    if len(position) >= 4:
        chars_per_line = max(2, int(position[2] * 650 / max(font_pt, 10)))
        geometric_lines = max(1, int(position[3] * 900 / max(font_pt, 10)))
        hinted_lines = int(zone.get("max_lines_hint") or geometric_lines)
        max_lines = max(1, min(geometric_lines, hinted_lines))
        if eligibility in ("title", "short_label", "decorative"):
            max_lines = 1
        geometric_max = chars_per_line * max_lines
    else:
        geometric_max = 0
    if original_length:
        lower = max(1, int(original_length * 0.55))
        upper = max(lower, int(original_length * 1.35 + 0.5))
    else:
        lower = 1
        upper = {"short_label": 6, "title": 14, "decorative": 6}.get(
            eligibility, 20
        )
    unconstrained_upper = upper
    if hinted_max > 0:
        upper = min(upper, hinted_max)
    if geometric_max > 0:
        upper = min(upper, geometric_max)
    # When the source template already overfills its box, a capacity cap can
    # push ``upper`` below the original-derived ``lower``. Collapsing both to
    # the same number makes the LLM hit an unnecessarily exact character
    # count (for example exactly 24 characters). Retain the normal 55% fit
    # tolerance within the final capacity instead.
    if upper < unconstrained_upper and lower >= upper:
        lower = max(1, int(upper * 0.55))
    return max(1, lower), max(1, upper)


def _source_candidates(outline_slide: dict) -> list[str]:
    values = [outline_slide.get("title", ""), *outline_slide.get("bullets", [])]
    candidates: list[str] = []
    for value in values:
        cleaned = _clean_mapping_text(value)
        if not cleaned:
            continue
        if cleaned not in candidates:
            candidates.append(cleaned)
        for fragment in re.split(r"[。；;，,：:]", cleaned):
            fragment = fragment.strip()
            if len(fragment) >= 2 and fragment not in candidates:
                candidates.append(fragment)
    return candidates


def _fit_source_copy(zone: dict, candidates: list[str], ordinal: int,
                     *, prefer_title: bool = False) -> str:
    """Choose complete source-grounded copy without slicing words or metrics.

    This is the last-resort deterministic path.  It may select or combine
    complete source fragments, but it must never make an invalid mapping look
    valid by cutting a string at an arbitrary character boundary.  When no
    complete fragment fits, the shortest complete candidate is returned and
    the caller marks it as overflow/needs-review.
    """
    lower, upper = _zone_target_range(zone)
    if not candidates:
        return ""
    eligibility = zone.get("content_eligibility", "body")
    ordered = list(candidates)
    if prefer_title:
        ordered = [candidates[0], *candidates[1:]]
    elif eligibility in ("short_label", "decorative"):
        ordered = sorted(candidates, key=lambda item: (len(item), candidates.index(item)))
    else:
        shift = ordinal % len(candidates)
        ordered = candidates[shift:] + candidates[:shift]

    fitting = [item for item in ordered if lower <= _visible_char_count(item) <= upper]
    if fitting:
        return fitting[0]

    # Combine only complete fragments.  This is useful for a body zone whose
    # minimum length is larger than any individual bullet, without creating
    # fragments such as ``WebSo`` or ``320m``.
    result = ""
    for candidate in ordered:
        addition = candidate if not result else f"；{candidate}"
        if _visible_char_count(result + addition) > upper:
            continue
        result += addition
        if _visible_char_count(result) >= lower:
            return result.rstrip("，,；;：:。 ")

    return min(ordered, key=lambda item: (_visible_char_count(item), ordered.index(item)))


def _protected_tokens(value: object) -> list[str]:
    """Return technical/metric tokens that must survive rewriting atomically."""
    text = _content_text(value)
    tokens = re.findall(
        r"(?:[A-Za-z][A-Za-z0-9_+.-]{2,}|\d+(?:\.\d+)?(?:%|ms|s|QPS|k\+|K\+|万\+)?|XSS/SQL)",
        text,
    )
    return list(dict.fromkeys(token for token in tokens if token))


def _zone_constraint(zone: dict) -> dict:
    """Build the exact, machine-checked writing contract sent to the LLM."""
    lower, upper = _zone_target_range(zone)
    position = zone.get("position", [0, 0, 0, 0])
    formatting = zone.get("formatting", {}) or {}
    font_pt = float(formatting.get("font_size_pt") or 14)
    geometric_lines = (
        max(1, int(position[3] * 900 / max(font_pt, 10)))
        if len(position) >= 4 else 1
    )
    max_lines = min(
        geometric_lines, int(zone.get("max_lines_hint") or geometric_lines)
    )
    if zone.get("content_eligibility") in {"title", "short_label", "decorative"}:
        max_lines = 1
    return {
        "zone_id": zone.get("zone_id", ""),
        "type": zone.get("type", "body"),
        "semantic_role": zone.get("semantic_role", ""),
        "content_eligibility": zone.get("content_eligibility", "body"),
        "original_text": zone.get("original_text", zone.get("text", "")),
        "min_chars": lower,
        "max_chars": upper,
        "max_lines": max(1, max_lines),
        "vertical_mode": zone.get("vertical_mode", "horizontal"),
        "is_narrow": bool(zone.get("is_narrow", False)),
        "position": position,
        "formatting": formatting,
    }


def _synchronize_layered_text_zones(zones: list[dict], template_slide: dict) -> None:
    """Give overlapping duplicate text layers identical replacement copy."""
    def substantially_overlaps(a: list, b: list) -> bool:
        if len(a) < 4 or len(b) < 4:
            return False
        ax1, ay1, aw, ah = (float(value) for value in a[:4])
        bx1, by1, bw, bh = (float(value) for value in b[:4])
        intersection_w = max(0.0, min(ax1 + aw, bx1 + bw) - max(ax1, bx1))
        intersection_h = max(0.0, min(ay1 + ah, by1 + bh) - max(ay1, by1))
        intersection = intersection_w * intersection_h
        smaller_area = min(max(aw * ah, 0.0), max(bw * bh, 0.0))
        return bool(smaller_area and intersection / smaller_area >= 0.5)

    mapped_by_id = {zone.get("zone_id", ""): zone for zone in zones}
    groups: list[list[dict]] = []
    for template_zone in template_slide.get("text_zones", []):
        original = _normalized_text(str(
            template_zone.get("original_text", template_zone.get("text", ""))
        ))
        position = template_zone.get("position", [])
        if not original or len(position) < 4:
            continue
        for group in groups:
            anchor = group[0]
            anchor_position = anchor.get("position", [])
            anchor_original = _normalized_text(str(
                anchor.get("original_text", anchor.get("text", ""))
            ))
            same_layer_copy = (
                original == anchor_original
                or SequenceMatcher(
                    None, original, anchor_original
                ).ratio() >= 0.9
            )
            close_geometry = (
                len(anchor_position) >= 4
                and max(
                    abs(float(position[i]) - float(anchor_position[i]))
                    for i in range(4)
                )
                <= 0.04
            )
            exact_source_copy = original == anchor_original
            if same_layer_copy and (
                close_geometry
                or (
                    exact_source_copy
                    and substantially_overlaps(position, anchor_position)
                )
            ):
                group.append(template_zone)
                break
        else:
            groups.append([template_zone])

    for group in groups:
        if len(group) < 2:
            continue
        mapped = [mapped_by_id.get(item.get("zone_id", "")) for item in group]
        mapped = [item for item in mapped if item and item.get("action") == "replace_text"]
        if len(mapped) < 2:
            continue
        lower = max(_zone_target_range(item)[0] for item in group)
        upper = min(_zone_target_range(item)[1] for item in group)
        canonical = next(
            (item.get("content") for item in mapped
             if lower <= _visible_char_count(item.get("content")) <= upper),
            mapped[0].get("content"),
        )
        for item in mapped:
            item["content"] = canonical
            item["placement_reason"] = (
                str(item.get("placement_reason", "")).rstrip("; ")
                + "; synchronized with overlapping template text layer"
            ).lstrip("; ")


def _assess_baked_text_risk(slide: dict) -> dict:
    """Classify text visible in pixels but absent from editable text shapes."""
    ocr_text = _normalized_text(str(slide.get("ocr_text", "")))
    editable_text = _normalized_text(" ".join(
        str(zone.get("original_text", zone.get("text", "")))
        for zone in slide.get("text_zones", [])
    ))
    large_image_area = sum(
        position[2] * position[3]
        for zone in slide.get("image_zones", [])
        if len((position := zone.get("position", []))) >= 4
        and position[2] * position[3] >= 0.12
    )

    if len(ocr_text) >= 8:
        similarity = SequenceMatcher(None, ocr_text, editable_text).ratio() if editable_text else 0.0
        unmatched_ratio = 1.0 - similarity
        level = "confirmed" if unmatched_ratio >= 0.45 and large_image_area > 0 else "low"
        return {
            "level": level,
            "unmatched_ocr_ratio": round(unmatched_ratio, 3),
            "large_image_area": round(large_image_area, 3),
        }

    return {
        "level": "unknown" if large_image_area >= 0.25 else "none",
        "unmatched_ocr_ratio": None,
        "large_image_area": round(large_image_area, 3),
    }


def _ensure_generated_content_image_zone(
    slide: dict,
    template_slide: dict,
    design_decision: dict,
    outline_slide: dict,
    image_inventory: list[dict],
) -> None:
    """Request one unique content image when ``user_image`` has no source.

    Template ingestion also sees full-slide raster decorations as image zones,
    so candidates are limited to plausible content frames. Remaining template
    pictures stay untouched; the selected frame gets its own semantic prompt
    and therefore its own output file.
    """
    strategy = str(design_decision.get("visual_strategy") or "").lower()
    if strategy not in {"user_image", "generated_region", "generated_image"}:
        return
    if image_inventory:
        return
    if any(
        zone.get("type") == "image"
        and zone.get("action") == "replace_image"
        for zone in slide.get("zones", [])
    ):
        return

    candidates: list[dict] = []
    for zone in template_slide.get("image_zones", []):
        position = zone.get("position", [])
        if len(position) < 4:
            continue
        x, y, width, height = (float(value) for value in position[:4])
        area = width * height
        if (
            0.015 <= area <= 0.20
            and width <= 0.50
            and height <= 0.70
            and x >= 0
            and y >= 0.12
            and x + width <= 1.02
            and y + height <= 1.02
        ):
            candidates.append(zone)
    if not candidates:
        return

    target = max(
        candidates,
        key=lambda zone: zone["position"][2] * zone["position"][3],
    )
    purposes = [
        str(block.get("purpose") or "").strip()
        for block in design_decision.get("block_plan", [])
        if block.get("block_type") == "image" and block.get("purpose")
    ]
    title = str(outline_slide.get("title") or "").strip()
    bullets = [
        str(item).strip()
        for item in outline_slide.get("bullets", [])[:2]
        if str(item).strip()
    ]
    prompt_parts = [title, *purposes, *bullets]
    prompt = "；".join(part for part in prompt_parts if part)
    slide.setdefault("zones", []).append({
        **target,
        "type": "image",
        "editable": False,
        "action": "replace_image",
        "source": "generated",
        "image_ref": None,
        "image_prompt": prompt or f"科技主题内容图，页面 {slide.get('slide_index', 0) + 1}",
        "fit_status": "unknown",
        "placement_reason": (
            "No user image was supplied; generate one unique semantic image "
            "for the largest plausible template content frame."
        ),
    })


def _template_slide_score(
    outline_slide: dict,
    template_slide: dict,
    design_decision: dict,
    template_count: int,
) -> float:
    text_zones = [
        zone for zone in template_slide.get("text_zones", [])
        if zone.get("type") != "footer"
        and zone.get("content_eligibility", "body") in ("title", "body")
        and len(zone.get("position", [])) >= 4
        and zone["position"][2] * zone["position"][3] >= 0.002
    ]
    all_editable_text = [
        zone for zone in template_slide.get("text_zones", [])
        if zone.get("editable", True) and zone.get("type") != "footer"
    ]
    bullet_count = len([
        item for item in outline_slide.get("bullets", []) if _clean_mapping_text(item)
    ])
    planned_blocks = len(design_decision.get("block_plan", []))
    desired_slots = max(1, planned_blocks or (1 + bullet_count))
    coverage = min(1.0, len(text_zones) / desired_slots)
    slot_mismatch_penalty = abs(len(all_editable_text) - desired_slots) * 0.08
    total_area = sum(zone["position"][2] * zone["position"][3] for zone in text_zones)
    capacity_score = min(1.0, total_area / max(0.05, desired_slots * 0.035))
    original_chars = sum(
        len(str(zone.get("original_text", zone.get("text", "")))) for zone in text_zones
    )
    residue_penalty = min(0.25, original_chars / 1200)
    risk = _assess_baked_text_risk(template_slide)
    baked_penalty = {"confirmed": 1.0, "unknown": 0.18, "low": 0.05, "none": 0.0}[risk["level"]]

    slide_type = str(outline_slide.get("type", "content"))
    layout = str(template_slide.get("layout", ""))
    index = int(template_slide.get("index", 0))
    semantic_bonus = 0.0
    title_length = _visible_char_count(outline_slide.get("title", ""))
    title_capacity_ok = any(
        zone.get("content_eligibility", "body") == "title"
        and _zone_target_range(zone)[1] >= min(title_length, 1)
        for zone in all_editable_text
    )
    if title_length and not title_capacity_ok:
        semantic_bonus -= 0.35
    if slide_type == "cover":
        semantic_bonus += 0.35 if (index == 0 or layout.startswith("cover")) else -0.2
    elif slide_type == "closing":
        semantic_bonus += 0.2 * (index / max(1, template_count - 1))
    elif layout.startswith("cover"):
        semantic_bonus -= 0.3

    return (
        coverage * 0.55 + capacity_score * 0.30 + semantic_bonus
        - slot_mismatch_penalty - residue_penalty - baked_penalty
    )


_TEMPLATE_ROLE_KEYWORDS = {
    "cover": ("封面", "项目名称", "大赛"),
    "background": ("项目背景", "行业背景", "应用场景", "市场规模"),
    "problem": ("行业痛点", "痛点", "问题", "挑战"),
    "innovation": ("核心技术", "技术创新", "解决办法", "研发"),
    "system_design": ("核心技术", "解决办法", "使用流程", "系统", "架构"),
    "feature_demo": ("核心技术", "使用流程", "性能", "检测", "工程实例"),
    "data": ("市场规模", "性能", "检测", "销售额", "数据"),
    "value": ("社会价值", "带动就业", "价值", "教育维度"),
    "roadmap": ("发展规划", "生产计划", "研发历程", "规划"),
    "closing": ("致谢", "展望未来", "发展规划"),
}


def _template_role_bonus(outline_slide: dict, template_slide: dict) -> float:
    """Reward source pages whose authored role matches the narrative role."""
    role = str(outline_slide.get("type") or "").strip().lower()
    keywords = _TEMPLATE_ROLE_KEYWORDS.get(role, ())
    if not keywords:
        return 0.0
    corpus = " ".join(
        _clean_mapping_text(
            zone.get("original_text")
            or zone.get("text")
            or zone.get("content")
            or ""
        )
        for zone in template_slide.get("text_zones", [])
    )
    matches = sum(1 for keyword in keywords if keyword in corpus)
    return min(1.5, matches * 0.75)


def _select_template_slides(outline: dict, template_zones: dict, design: dict) -> list[int]:
    """Select an ordered, unique set of semantically compatible source pages.

    A global dynamic program avoids the old greedy failure where one early
    slide jumped to a late, high-capacity page and forced every remaining
    output onto the final consecutive block of the template.
    """
    template_slides = sorted(template_zones.get("slides", []), key=lambda slide: slide["index"])
    outline_slides = outline.get("slides", [])
    if not template_slides:
        return [slide.get("slide_index", index) for index, slide in enumerate(outline_slides)]
    if len(template_slides) < len(outline_slides):
        return [template_slides[index % len(template_slides)]["index"] for index in range(len(outline_slides))]

    design_by_index = {item.get("slide_index"): item for item in design.get("slides", [])}
    output_count = len(outline_slides)
    template_count = len(template_slides)
    negative_infinity = float("-inf")
    scores = [
        [negative_infinity] * template_count
        for _ in range(output_count)
    ]
    previous = [
        [-1] * template_count
        for _ in range(output_count)
    ]

    def page_score(outline_position: int, template_position: int) -> float:
        outline_slide = outline_slides[outline_position]
        template_slide = template_slides[template_position]
        decision = design_by_index.get(outline_slide.get("slide_index"), {})
        expected_position = (
            outline_position * (template_count - 1) / max(1, output_count - 1)
        )
        progression_penalty = 0.28 * abs(
            template_position - expected_position
        )
        return (
            _template_slide_score(
                outline_slide, template_slide, decision, template_count
            )
            + _template_role_bonus(outline_slide, template_slide)
            - progression_penalty
        )

    for template_position in range(0, template_count - output_count + 1):
        scores[0][template_position] = page_score(0, template_position)

    for outline_position in range(1, output_count):
        min_template_position = outline_position
        max_template_position = template_count - (
            output_count - outline_position
        )
        for template_position in range(
            min_template_position, max_template_position + 1
        ):
            predecessor = max(
                range(outline_position - 1, template_position),
                key=lambda position: scores[outline_position - 1][position],
            )
            predecessor_score = scores[outline_position - 1][predecessor]
            if predecessor_score == negative_infinity:
                continue
            scores[outline_position][template_position] = (
                predecessor_score
                + page_score(outline_position, template_position)
            )
            previous[outline_position][template_position] = predecessor

    last_position = max(
        range(output_count - 1, template_count),
        key=lambda position: scores[output_count - 1][position],
    )
    selected_positions: list[int] = []
    for outline_position in range(output_count - 1, -1, -1):
        selected_positions.append(last_position)
        last_position = previous[outline_position][last_position]
    selected_positions.reverse()
    return [template_slides[position]["index"] for position in selected_positions]


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
    design_by_index = {int(item["slide_index"]): item for item in design.get("slides", [])}
    tpl_slides = {}
    selected_slide_indices: list[int] = []
    if template_zones:
        tpl_slides = {s["index"]: s for s in template_zones.get("slides", [])}
        selected_slide_indices = _select_template_slides(outline, template_zones, design)

    slides = []
    for outline_position, slide in enumerate(outline["slides"]):
        idx = slide["slide_index"]
        decision = design_by_index.get(idx, {})
        template_slide_index = (
            selected_slide_indices[outline_position]
            if outline_position < len(selected_slide_indices) else idx
        )
        tpl_slide = tpl_slides.get(template_slide_index, {})
        # A source screenshot is evidence, not wallpaper.  The deterministic
        # mapper consumes each uploaded image at most once across the deck;
        # the Mapping Agent may make a different explicit choice when it has
        # semantic evidence for reuse.
        image = (
            image_inventory[outline_position]
            if outline_position < len(image_inventory)
            else None
        )

        # Build zones from template structure or defaults
        zones = _build_zones_for_slide(slide, tpl_slide, image, idx)

        # Get the template image path for this slide (for img2img)
        template_image = tpl_slide.get("template_image")
        baked_risk = _assess_baked_text_risk(tpl_slide) if tpl_slide else {"level": "none"}
        selection_score = _template_slide_score(
            slide, tpl_slide, decision, max(1, len(tpl_slides))
        ) if tpl_slide else 0.0
        fallback_flags = [] if image else ["visual_placeholder"]
        if baked_risk.get("level") == "confirmed":
            fallback_flags.append("template_slide_incompatible:baked_text")

        slides.append(
            SlideContent(
                slide_index=idx,
                layout=decision.get("layout_id", tpl_slide.get("layout", "fallback.basic")),
                layout_id=decision.get("layout_id", tpl_slide.get("layout", "fallback.basic")),
                visual_density=decision.get("visual_density", "medium"),
                review_status="needs_review" if baked_risk.get("level") == "confirmed" else "draft",
                zones=zones,
                source_refs=slide.get("source_refs", []),
                fallback_flags=fallback_flags,
                template_image=template_image,
                template_slide_index=template_slide_index,
                template_selection={
                    "score": round(selection_score, 4),
                    "baked_text_risk": baked_risk,
                    "reason": "best compatible page under ordered unique selection",
                },
            ).to_dict()
        )

    return {
        "template_id": selected.get("template_id") or selected.get("selected_template_id", "fallback.default"),
        "review_status": aggregate_review_status(slides),
        "assembly_policy": {
            "mode": "text_replace_only",
            "allow_overlay": False,
            "allow_resize": False,
            "allow_font_change": False,
            "allow_position_change": False,
            "require_all_text_zones_actioned": True,
            "require_nonempty_text": True,
            "allow_clear_text": False,
            "match_original_length": True,
            "target_length_ratio": [0.55, 1.35],
            "require_render_validation": True,
        },
        "slides": slides,
    }


def _build_zones_for_slide(
    outline_slide: dict,
    tpl_slide: dict,
    image: dict | None,
    slide_index: int,
) -> list[SlideZoneContent]:
    """Build an explicit, unlimited zone action plan for fallback mode.

    Every editable template text zone is represented.  ``zone_id`` is only
    the execution address; content distribution is based on semantic type,
    geometry-derived capacity, and the available content blocks.
    """
    text_zones = list(tpl_slide.get("text_zones", []))
    image_zones = list(tpl_slide.get("image_zones", []))
    result: list[SlideZoneContent] = []
    desired_title_length = _visible_char_count(outline_slide.get("title", ""))

    def title_rank(zone: dict) -> tuple[int, int, int, float, float]:
        zone_type = zone.get("type", "body")
        font_size = (zone.get("formatting", {}) or {}).get("font_size_pt") or 0
        pos = zone.get("position", [0, 0, 0, 0])
        area = pos[2] * pos[3] if len(pos) >= 4 else 0
        capacity = _zone_target_range(zone)[1]
        shortfall = max(0, desired_title_length - capacity)
        return (
            0 if shortfall == 0 else 1,
            shortfall,
            {"title": 0, "subtitle": 1}.get(zone_type, 2),
            -float(font_size),
            -area,
        )

    title_candidates = [
        zone for zone in text_zones
        if zone.get("content_eligibility", "body") in ("title", "body")
        and zone.get("type") != "footer"
    ]
    title_zone = min(title_candidates, key=title_rank) if title_candidates else None
    source_block_ids = [str(ref) for ref in outline_slide.get("source_refs", [])]
    candidates = _source_candidates(outline_slide)
    replacement_ordinal = 0

    for zone in text_zones:
        zone_id = zone.get("zone_id", "")
        position = zone.get("position", [0.1, 0.1, 0.8, 0.8])
        formatting = zone.get("formatting")
        zone_type = zone.get("type", "body")
        original_text = _clean_mapping_text(
            zone.get("original_text", zone.get("text", ""))
        )

        # Fixed footer/page chrome remains untouched when it already has text.
        if zone_type == "footer" and original_text:
            result.append(SlideZoneContent(
                zone_id, "footer", position, True, original_text, "template",
                fit_status="fits", formatting=formatting,
                action="preserve",
                placement_reason="preserve non-empty template footer/page chrome",
            ))
        else:
            content = _fit_source_copy(
                zone, candidates, replacement_ordinal,
                prefer_title=zone is title_zone,
            )
            replacement_ordinal += 1
            output_type = "title" if zone is title_zone else zone_type
            target_min, target_max = _zone_target_range(zone)
            content_length = _visible_char_count(content)
            fit_status = (
                "fits" if target_min <= content_length <= target_max else "overflow"
            )
            result.append(SlideZoneContent(
                zone_id, output_type, position, True,
                content, "generated",
                fit_status=fit_status, formatting=formatting,
                action="replace_text",
                placement_reason=(
                    "last-resort complete source fragment selected without hard truncation"
                ),
                transformation="rewrite" if source_block_ids else "none",
                source_block_ids=source_block_ids,
            ))

    for index, zone in enumerate(image_zones):
        should_replace = bool(image) and index == 0
        result.append(SlideZoneContent(
            zone.get("zone_id", ""), "image",
            zone.get("position", [0.7, 0.3, 0.2, 0.4]), False,
            None, "user_upload" if should_replace else "template",
            image_ref=image.get("path") if should_replace else None,
            image_prompt=outline_slide.get("image_needs"),
            fit_status="unknown", formatting=zone.get("formatting"),
            action="replace_image" if should_replace else "preserve",
            placement_reason=(
                "use the most relevant user image once"
                if should_replace else "preserve remaining template visual"
            ),
        ))

    if not text_zones:
        logger.warning(
            "Slide %d has no editable text zones; strict mapping requires template reselection",
            slide_index,
        )
    return result


def _build_zones_for_slide_legacy(
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
        used_zone_ids: set[str] = set()  # prevent duplicate zone_id assignment

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
            zid = tz.get("zone_id", f"{zone_type}_{slide_index}")

            if zone_type == "title" and not title_assigned:
                zones.append(SlideZoneContent(
                    zid, "title", position, True,
                    outline_slide["title"], "generated", fit_status="fits",
                    formatting=formatting,
                ))
                title_assigned = True
                used_zone_ids.add(zid)
            elif zone_type in ("subtitle",) and not title_assigned:
                zones.append(SlideZoneContent(
                    zid, "title", position, True,
                    outline_slide["title"], "generated", fit_status="fits",
                    formatting=formatting,
                ))
                title_assigned = True
                used_zone_ids.add(zid)
            elif zone_type in ("body", "subtitle") and not body_assigned:
                zones.append(SlideZoneContent(
                    zid, "bullets", position, True,
                    outline_slide.get("bullets", []), "generated", fit_status="fits",
                    formatting=formatting,
                ))
                body_assigned = True
                used_zone_ids.add(zid)
            elif zone_type == "footer":
                zones.append(SlideZoneContent(
                    zid, "footer", position, True,
                    "", "generated", fit_status="fits",
                    formatting=formatting,
                ))
                used_zone_ids.add(zid)

        if not title_assigned:
            tid = _find_fallback_zone_id("title", tpl_slide) or f"_overlay_{slide_index}_title"
            zones.insert(0, SlideZoneContent(
                tid, "title", [0.08, 0.08, 0.84, 0.16], True,
                outline_slide["title"], "generated", fit_status="fits",
            ))
        if not body_assigned:
            bid = _find_fallback_zone_id("body", tpl_slide) or f"_overlay_{slide_index}_body"
            zones.append(SlideZoneContent(
                bid, "bullets", [0.10, 0.28, 0.56, 0.54], True,
                outline_slide.get("bullets", []), "generated", fit_status="fits",
            ))
    else:
        tid = _find_fallback_zone_id("title", tpl_slide) or f"_overlay_{slide_index}_title"
        bid = _find_fallback_zone_id("body", tpl_slide) or f"_overlay_{slide_index}_body"
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


def _fixed_template_text(zone: dict) -> bool:
    """Whether template text is intentional chrome rather than sample copy."""
    role = str(zone.get("semantic_role", "")).lower()
    zone_type = str(zone.get("type", "")).lower()
    return zone_type == "footer" or role in {
        "fixed_brand", "brand", "page_chrome", "page_number", "footer",
    }


def _zone_repair_reasons(mapped: dict | None, template_zone: dict) -> list[str]:
    """Return reasons why an editable text zone needs an LLM rewrite."""
    if _fixed_template_text(template_zone):
        return []
    if not mapped:
        return ["missing_zone"]

    reasons: list[str] = []
    action = mapped.get("action")
    content = mapped.get("content")
    if action != "replace_text":
        reasons.append("sample_copy_must_be_replaced")
    if not _clean_mapping_text(content):
        reasons.append("empty_content")
    if action == "replace_text" and content not in (None, "", []):
        lower, upper = _zone_target_range(template_zone)
        length = _visible_char_count(content)
        if not lower <= length <= upper:
            reasons.append(f"length_{length}_outside_{lower}_{upper}")
        if isinstance(content, list):
            constraint = _zone_constraint(template_zone)
            position = template_zone.get("position", [0, 0, 0, 0])
            formatting = template_zone.get("formatting", {}) or {}
            font_pt = float(formatting.get("font_size_pt") or 14)
            chars_per_line = (
                max(2, int(position[2] * 650 / max(font_pt, 10)))
                if len(position) >= 4 else upper
            )
            max_lines = int(constraint["max_lines"])
            if len(content) > max_lines:
                reasons.append(f"line_count_{len(content)}_outside_{max_lines}")
            if any(_visible_char_count(item) > chars_per_line for item in content):
                reasons.append(f"line_width_outside_{chars_per_line}")
    rendered = _clean_mapping_text(content)
    if rendered.lower() in {"u", "v", "w", "•", "●"}:
        reasons.append("orphan_bullet_or_glyph")
    return reasons


def _llm_zone_repair_reasons(mapped: dict | None, template_zone: dict) -> list[str]:
    """Return reasons a zone still needs an LLM-authored final decision."""
    reasons = _zone_repair_reasons(mapped, template_zone)
    if (
        not _fixed_template_text(template_zone)
        and mapped
        and mapped.get("source") != "llm"
    ):
        reasons.append("non_llm_copy")
    return reasons


def _reconcile_zone_repair_flags(slide: dict, template_slide: dict) -> None:
    """Remove unresolved markers that no longer describe the final zone state."""
    mapped_by_id = {
        zone.get("zone_id", ""): zone for zone in slide.get("zones", [])
        if zone.get("zone_id")
    }
    template_by_id = {
        zone.get("zone_id", ""): zone
        for zone in template_slide.get("text_zones", [])
        if zone.get("zone_id") and zone.get("editable", True)
    }
    unresolved = {
        zone_id for zone_id, template_zone in template_by_id.items()
        if _zone_repair_reasons(mapped_by_id.get(zone_id), template_zone)
    }
    prefixes = (
        "llm_constraint_unresolved:",
        "missing_zone_unresolved:",
        "llm_micro_repair_unresolved:",
        "length_mismatch:",
        "untraceable_rewrite:",
        "overflow:",
    )
    reconciled: list[str] = []
    for flag in slide.get("fallback_flags", []):
        rendered = str(flag)
        matched_prefix = next(
            (prefix for prefix in prefixes if rendered.startswith(prefix)), None
        )
        if matched_prefix and rendered[len(matched_prefix):] not in unresolved:
            continue
        reconciled.append(rendered)
    slide["fallback_flags"] = list(dict.fromkeys(reconciled))


def _outline_source_blocks(outline_slide: dict, slide_index: int) -> list[dict]:
    """Expose compact, stable source block IDs for micro-rewrite traceability."""
    blocks: list[dict] = []
    title = _clean_mapping_text(outline_slide.get("title", ""))
    if title:
        blocks.append({"block_id": f"slide{slide_index}_title", "text": title})
    for bullet_index, bullet in enumerate(outline_slide.get("bullets", []), start=1):
        text = _clean_mapping_text(bullet)
        if text:
            blocks.append({
                "block_id": f"slide{slide_index}_bullet{bullet_index}",
                "text": text,
            })
    return blocks


def _micro_repair_candidate(
    repair: dict,
    template_zone: dict,
    current_zone: dict | None,
    default_source_ids: list[str],
) -> dict | None:
    """Build and locally accept only a genuinely fitting semantic rewrite."""
    zone_id = template_zone.get("zone_id", "")
    if repair.get("zone_id") != zone_id:
        return None

    requested_source_ids = [
        str(source_id) for source_id in repair.get("source_block_ids", [])
        if str(source_id) in default_source_ids
    ]
    current_source_ids = [
        str(source_id) for source_id in (current_zone or {}).get("source_block_ids", [])
        if str(source_id) in default_source_ids
    ]
    candidate = dict(current_zone or {})
    candidate.update({
        "zone_id": zone_id,
        "type": template_zone.get("type", candidate.get("type", "body")),
        "position": template_zone.get(
            "position", candidate.get("position", [0.1, 0.1, 0.8, 0.1])
        ),
        "formatting": template_zone.get("formatting", candidate.get("formatting")),
        "editable": template_zone.get("editable", True),
        "action": "replace_text",
        "content": repair.get("content"),
        "placement_reason": repair.get("placement_reason")
        or "targeted semantic rewrite for exact zone capacity",
        "source_block_ids": requested_source_ids
        or current_source_ids
        or default_source_ids,
        "transformation": repair.get("transformation") or "rewrite",
        "fit_status": "fits",
        "source": "llm",
    })
    candidate.pop("_warning", None)
    if _zone_repair_reasons(candidate, template_zone):
        return None
    return candidate


def _merge_llm_zone_repairs(slide: dict, repaired_slide: dict, target_ids: set[str]) -> int:
    """Merge only explicitly requested, valid LLM repair zones."""
    current = {zone.get("zone_id", ""): zone for zone in slide.get("zones", [])}
    merged = 0
    for zone in repaired_slide.get("zones", []):
        zone_id = zone.get("zone_id", "")
        if zone_id not in target_ids:
            continue
        zone["source"] = "llm"
        zone.setdefault("fit_status", "unknown")
        current[zone_id] = zone
        merged += 1

    # Preserve original order and append zones that were missing in the first
    # response.  Template locking later restores position/formatting.
    seen: set[str] = set()
    ordered: list[dict] = []
    for zone in slide.get("zones", []):
        zone_id = zone.get("zone_id", "")
        if zone_id in current and zone_id not in seen:
            ordered.append(current[zone_id])
            seen.add(zone_id)
    for zone_id, zone in current.items():
        if zone_id and zone_id not in seen:
            ordered.append(zone)
    slide["zones"] = ordered
    return merged


def _repair_zones_in_micro_batches(
    llm_client,
    slide: dict,
    outline_slide: dict,
    neighboring_slides: list[dict],
    design_decision: dict,
    source_summary: dict,
    template_slide: dict,
) -> int:
    """Repair remaining zones in tiny batches, then retry only rejected zones.

    The normal fit pass works at slide scope.  This second level deliberately
    uses a much smaller response schema so a long/complex slide cannot lose
    zones to output truncation or JSON repair.
    """
    slide_index = int(slide.get("slide_index", -1))
    template_index = int(slide.get("template_slide_index", -1))
    source_blocks = _outline_source_blocks(outline_slide, slide_index)
    all_source_ids = [block["block_id"] for block in source_blocks]
    protected_tokens = _protected_tokens(
        [block["text"] for block in source_blocks]
    )

    def collect_targets(zone_ids: set[str] | None = None) -> list[dict]:
        mapped_by_id = {
            zone.get("zone_id", ""): zone for zone in slide.get("zones", [])
            if zone.get("zone_id")
        }
        targets: list[dict] = []
        for template_zone in template_slide.get("text_zones", []):
            zone_id = template_zone.get("zone_id", "")
            if (
                not zone_id
                or not template_zone.get("editable", True)
                or (zone_ids is not None and zone_id not in zone_ids)
            ):
                continue
            reasons = _llm_zone_repair_reasons(
                mapped_by_id.get(zone_id), template_zone
            )
            if not reasons:
                continue
            constraint = _zone_constraint(template_zone)
            constraint.update({
                "repair_reasons": reasons,
                "current_content": _content_text(
                    (mapped_by_id.get(zone_id) or {}).get("content")
                ),
                "protected_tokens": protected_tokens,
            })
            zone_type = str(template_zone.get("type", "body"))
            if zone_type in {"title", "subtitle"}:
                default_ids = [
                    block_id for block_id in all_source_ids
                    if block_id.endswith("_title")
                ] or all_source_ids[:1]
            else:
                default_ids = [
                    block_id for block_id in all_source_ids
                    if "_bullet" in block_id
                ] or all_source_ids[:1]
            constraint["allowed_source_block_ids"] = all_source_ids
            constraint["default_source_block_ids"] = default_ids
            targets.append(constraint)
        return targets

    targets = collect_targets()
    if not targets:
        return 0

    prompt = """
You are repairing only the listed PowerPoint text zones. Return a JSON object
with a `repairs` array containing exactly one item for every requested zone_id.
Write new, natural, source-grounded copy; do not merely truncate the current
text. Visible character count is a hard constraint and includes letters,
digits, punctuation, and Chinese characters but excludes whitespace. Use only
allowed_source_block_ids. Keep protected technical tokens atomic when their
fact is used. Do not change layout, formatting, font, size, or zone_id. Do not
return any unrequested zone or slide-level data.
""".strip()

    # Keep micro-repair genuinely small. The target constraints and this
    # slide's source blocks contain all evidence required for a grounded
    # rewrite; project-wide and neighbouring-slide context made a one-zone
    # repair almost as large as a full mapping call.
    common_context = {
        "slide_index": slide_index,
        "template_slide_index": template_index,
        "slide_title": outline_slide.get("title", ""),
        "source_blocks": source_blocks,
    }

    repaired_count = 0

    def run_call(call_targets: list[dict], phase: str, feedback: str = "") -> set[str]:
        nonlocal repaired_count
        if not call_targets:
            return set()
        result = llm_client.generate_json(
            prompt=prompt,
            context={
                **common_context,
                "repair_targets": call_targets,
                "validation_feedback": feedback,
            },
            system="You are a precise presentation micro-copy editor. Output JSON only.",
            phase=phase,
            fallback={"repairs": []},
            max_tokens=min(2400, max(900, len(call_targets) * 420)),
            temperature=0.0,
            json_schema=_ZONE_REPAIR_SCHEMA,
            schema_name="content_mapping_zone_repairs",
        )
        if getattr(llm_client, "was_fallback", lambda _phase: False)(phase):
            return set()

        if not isinstance(result, dict):
            logger.warning(
                "Ignoring invalid mapping repair response for %s: expected object, got %s",
                phase,
                type(result).__name__,
            )
            return set()
        repairs = result.get("repairs")
        if not isinstance(repairs, list):
            logger.warning(
                "Ignoring invalid mapping repair response for %s: repairs is %s",
                phase,
                type(repairs).__name__,
            )
            return set()

        target_by_id = {target["zone_id"]: target for target in call_targets}
        template_by_id = {
            zone.get("zone_id", ""): zone
            for zone in template_slide.get("text_zones", [])
        }
        current_by_id = {
            zone.get("zone_id", ""): zone for zone in slide.get("zones", [])
        }
        candidates: list[dict] = []
        accepted: set[str] = set()
        for repair_index, repair in enumerate(repairs):
            if not isinstance(repair, dict):
                logger.warning(
                    "Ignoring invalid repair item for %s at index %d: got %s",
                    phase,
                    repair_index,
                    type(repair).__name__,
                )
                continue
            zone_id = repair.get("zone_id", "")
            if zone_id not in target_by_id or zone_id in accepted:
                continue
            target = target_by_id[zone_id]
            candidate = _micro_repair_candidate(
                repair,
                template_by_id[zone_id],
                current_by_id.get(zone_id),
                target.get("default_source_block_ids", []),
            )
            if candidate is None:
                continue
            candidates.append(candidate)
            accepted.add(zone_id)
        if candidates:
            repaired_count += _merge_llm_zone_repairs(
                slide, {"zones": candidates}, accepted
            )
        return accepted

    diagnostic_max_batches = _env_optional_nonnegative_int(
        "PPT_AGENT_MAPPING_MAX_BATCHES"
    )
    diagnostic_max_retries = _env_optional_nonnegative_int(
        "PPT_AGENT_MAPPING_MAX_RETRIES"
    )
    batch_size = 2
    for batch_number, start in enumerate(range(0, len(targets), batch_size), start=1):
        if (
            diagnostic_max_batches is not None
            and batch_number > diagnostic_max_batches
        ):
            break
        batch = targets[start:start + batch_size]
        phase = f"content_mapping_micro_{slide_index}_{batch_number}"
        accepted = run_call(batch, phase)
        rejected_ids = {target["zone_id"] for target in batch} - accepted
        for retry_number, zone_id in enumerate(sorted(rejected_ids), start=1):
            if (
                diagnostic_max_retries is not None
                and retry_number > diagnostic_max_retries
            ):
                break
            retry_target = collect_targets({zone_id})
            if not retry_target:
                continue
            run_call(
                retry_target,
                f"{phase}_retry_{retry_number}",
                "The prior response omitted this zone or violated its exact character range. "
                "Return exactly one valid semantic rewrite for this zone.",
            )

    unresolved = collect_targets()
    flags = list(slide.get("fallback_flags", []))
    if repaired_count:
        slide["llm_micro_repair_count"] = (
            int(slide.get("llm_micro_repair_count", 0)) + repaired_count
        )
    if not unresolved:
        recovered_prefixes = (
            "llm_fit_retry_failed:", "llm_fit_retry_incomplete:",
            "llm_mapping_fallback:", "missing_llm_slide:",
            "llm_micro_repair_unresolved:",
        )
        flags = [
            flag for flag in flags
            if not str(flag).startswith(recovered_prefixes)
        ]
        if repaired_count:
            flags.append(f"llm_micro_repair_recovered:{repaired_count}")
    else:
        unresolved_ids = {target["zone_id"] for target in unresolved}
        flags = [
            flag for flag in flags
            if not str(flag).startswith("llm_micro_repair_unresolved:")
        ]
        flags.extend(
            f"llm_micro_repair_unresolved:{zone_id}"
            for zone_id in sorted(unresolved_ids)
        )
    slide["fallback_flags"] = list(dict.fromkeys(flags))
    return repaired_count


def _repair_mapping_zones_with_llm(
    llm_client,
    mapping: dict,
    outline: dict,
    design: dict,
    source_summary: dict,
    template_zones: dict | None,
) -> None:
    """Repair only missing, non-LLM, or invalid zones with micro LLM calls."""
    if not template_zones:
        return

    template_by_index = {
        int(slide.get("index", index)): slide
        for index, slide in enumerate(template_zones.get("slides", []))
    }
    outline_by_index = {
        int(slide.get("slide_index", index)): slide
        for index, slide in enumerate(outline.get("slides", []))
    }
    design_by_index = {
        int(slide.get("slide_index", index)): slide
        for index, slide in enumerate(design.get("slides", []))
    }
    diagnostic_slide = os.getenv("PPT_AGENT_MAPPING_SLIDE", "").strip()
    diagnostic_slide_index = (
        int(diagnostic_slide) if diagnostic_slide else None
    )

    for slide in mapping.get("slides", []):
        slide_index = int(slide.get("slide_index", -1))
        if (
            diagnostic_slide_index is not None
            and slide_index != diagnostic_slide_index
        ):
            continue
        template_index = int(slide.get("template_slide_index", -1))
        template_slide = template_by_index.get(template_index)
        outline_slide = outline_by_index.get(slide_index)
        if not template_slide or not outline_slide:
            continue

        repaired = _repair_zones_in_micro_batches(
            llm_client,
            slide,
            outline_slide,
            [
                outline_by_index[index]
                for index in (slide_index - 1, slide_index + 1)
                if index in outline_by_index
            ],
            design_by_index.get(slide_index, {}),
            source_summary,
            template_slide,
        )
        if repaired:
            slide["llm_repair_count"] = (
                int(slide.get("llm_repair_count", 0)) + repaired
            )


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
        "template_id": selected.get("template_id") or selected.get("selected_template_id", "fallback.default"),
        "design_plan": {
            "theme_profile": design.get("theme_profile", {}),
            "slides": design.get("slides", []),
        },
        "image_inventory": image_info,
        "source_context": {
            "project": source_summary.get("project", {}),
            "core_topic": source_summary.get("core_topic", ""),
            "value_proposition": source_summary.get("value_proposition", ""),
            "audience": source_summary.get("audience", ""),
            "evidence": source_summary.get("evidence", []),
            "key_facts": source_summary.get("key_facts", []),
        },
    }

    # Include template zone structure if available
    if template_zones:
        context["template_zones"] = {
            "color_scheme": template_zones.get("color_scheme", ""),
            "slides": [
                {
                    "index": s["index"],
                    # Text zones are represented once in all_zones below.
                    # Image/decorative shapes are compact context only and do
                    # not need verbose per-shape output from the LLM.
                    "text_zones": [],
                    "image_zones": [
                        {
                            "zone_id": z.get("zone_id", ""),
                            "position": z.get("position", [0, 0, 0, 0]),
                            "type": z.get("type", "image"),
                        }
                        for z in s.get("image_zones", [])
                    ],
                    "all_zones": [
                        {
                            "zone_id": z.get("zone_id", ""),
                            "legacy_zone_id": z.get("legacy_zone_id", ""),
                            "native_shape_id": z.get("native_shape_id"),
                            "shape_path": z.get("shape_path", ""),
                            "type": z.get("type", "body"),
                            "position": z.get("position", [0, 0, 0, 0]),
                            "original_text": z.get("original_text", z.get("text", "")),
                            "editable": z.get("editable", False),
                            "shape_depth": z.get("shape_depth", 0),
                            "parent_group_ids": z.get("parent_group_ids", []),
                            "semantic_role": z.get("semantic_role", ""),
                            "content_eligibility": z.get("content_eligibility", "body"),
                            "supports_long_text": z.get("supports_long_text", False),
                            "max_chars_hint": z.get("max_chars_hint"),
                            "max_lines_hint": z.get("max_lines_hint"),
                            "effective_rotation_deg": z.get("effective_rotation_deg", 0),
                            "vertical_mode": z.get("vertical_mode", "horizontal"),
                            "is_narrow": z.get("is_narrow", False),
                            "formatting": z.get("formatting", {}),
                            "visual": z.get("visual", {}),
                        }
                        for z in s.get("all_zones", [])
                        if (
                            z.get("editable", False)
                            or z.get("type") in {"title", "subtitle", "bullets", "body", "footer", "decorative"}
                            or _clean_mapping_text(z.get("original_text", z.get("text", "")))
                        )
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
                constraint = _zone_constraint(z)
                z["min_chars"] = constraint["min_chars"]
                z["max_chars"] = constraint["max_chars"]
                z["max_lines"] = constraint["max_lines"]

    # ── AI Direct Zone Assignment prompt ──
    _legacy_assignment_instruction = """
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
- Rewrite concise labels and summaries when supported by source facts
- Use any number of editable zones; there is no three-zone limit

Every zone must include an explicit action: replace_text, clear_text,
preserve, or replace_image. Include source_block_ids and transformation for
adapted wording. Never request an overlay, resize, position, or font change.

### Critical rules
1. **zone_id**: COPY the exact zone_id from the template all_zones. Do NOT invent IDs.
2. **position**: COPY the position array from the template zone. Do NOT calculate or guess.
3. **formatting**: Preserve any existing formatting from the template zone.
4. **placement_reason**: Give a reason for EVERY zone — filled or empty.
   For filled zones: why this zone for this content. For empty: why left empty
   (e.g. "decorative element, no content needed").
5. **image zones**: Keep content: null and the original image_prompt.
6. **capacity is HARD LIMIT**: Read capacity_hint on each zone.
   - title zone (28pt+): 20 chars max, 1 line
   - body zone (12-14pt): 2-4 lines, 25-40 chars per line
   - footer: 1 line, 50 chars max
   Shorten or split content to fit. Never exceed the zone's capacity.
7. **Output EVERY zone**: You MUST include EVERY zone from the template all_zones
8. **Respect eligibility**: long prose may only use zones where content_eligibility is body and supports_long_text is true. Use short_label zones only for concise labels within max_chars_hint; otherwise clear_text. Never put prose into rotated, vertical, narrow, or decorative zones.
   list. Even empty zones — set content: null with a reason like "decorative".
8. Output ONLY the JSON object — no markdown, no explanation outside the JSON.
"""

    assignment_instruction = """
## Complete, Format-Preserving Zone Assignment

Fill EVERY editable text zone. Use `replace_text` for template sample copy and
`preserve` only for intentional, non-empty fixed footer/page/brand chrome.
`clear_text` is forbidden. Generate source-grounded titles, summaries, metrics,
captions, and short labels as needed; never invent facts.

Copy the exact zone_id and use the schema-allowed mapped type. Position and formatting are
immutable input-only fields: do not return them because the runtime copies
them from the template after generation. Do not add text boxes or change
geometry, font, font size, paragraph formatting, or alignment. Long prose is
allowed only where content_eligibility=body and
supports_long_text=true. Rotated, vertical, narrow, decorative, and short_label
zones must receive concise labels rather than prose.

Replacement visible character count must stay within the exact min_chars and
max_chars supplied for that zone and must not exceed max_lines. Every editable
text zone must contain non-empty text after assembly. Output every editable
template text zone with placement_reason, source_block_ids, transformation, and
fit_status. Image and purely decorative non-text shapes are preserved by the
runtime and do not need to be echoed.
Output JSON only.
"""
    enhanced_prompt = (prompt + "\n\n" + assignment_instruction).strip()
    full_fallback = _fallback_mapping(outline, selected, design, source_summary, template_zones)

    # ── Batching: process slides in groups to avoid output token truncation ──
    BATCH_SIZE = 2   # 2 slides per batch — avoids 16k output token truncation
    outline_slides = outline.get("slides", [])
    all_batch_slides: list[dict] = []

    for batch_start in range(0, len(outline_slides), BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, len(outline_slides))
        batch_outline_slides = outline_slides[batch_start:batch_end]
        expected_indices = [int(slide["slide_index"]) for slide in batch_outline_slides]

        # Build per-batch context — only the matching slides
        batch_context: dict = {
            "template_id": context["template_id"],
            "image_inventory": context["image_inventory"],
            "source_context": context["source_context"],
            "outline": {
                "meta": context["outline"]["meta"],
                "slides": batch_outline_slides,
            },
            "expected_slide_indices": expected_indices,
            "design_plan": {
                "theme_profile": context["design_plan"].get("theme_profile", {}),
                "slides": [s for s in context["design_plan"].get("slides", [])
                          if int(s.get("slide_index", -1)) in expected_indices],
            },
        }
        if "template_zones" in context:
            selected_template_indices = {
                slide.get("template_slide_index")
                for slide in full_fallback.get("slides", [])
                if int(slide.get("slide_index", -1)) in expected_indices
            }
            batch_context["template_zones"] = {
                "color_scheme": context["template_zones"]["color_scheme"],
                "slides": [s for s in context["template_zones"]["slides"]
                          if s["index"] in selected_template_indices],
            }

        batch_fallback = {
            "template_id": full_fallback["template_id"],
            "review_status": full_fallback["review_status"],
            "slides": [s for s in full_fallback["slides"]
                      if int(s["slide_index"]) in expected_indices],
        }

        logger.info("Content mapping batch %d-%d / %d slides",
                    batch_start + 1, batch_end, len(outline_slides))

        from ppt_agent.llm.schemas import (
            build_content_mapping_schema,
            normalize_content_mapping_response,
        )

        batch_schema = build_content_mapping_schema(
            batch_fallback["template_id"],
            batch_fallback["slides"],
        )

        batch_phase = f"content_mapping_{batch_start}"
        result = llm_client.generate_json(
            prompt=enhanced_prompt,
            context=batch_context,
            system="You are a content editor for PPT generation. Output only valid JSON.",
            phase=batch_phase,
            fallback=batch_fallback,
            max_tokens=16000,
            temperature=0.1,
            json_schema=batch_schema,
            schema_name="content_mapping_batch",
        )
        result = normalize_content_mapping_response(result)

        batch_slides = result.get("slides", [])
        batch_was_fallback = getattr(
            llm_client, "was_fallback", lambda _phase: False
        )(batch_phase)
        fallback_by_index = {
            int(slide["slide_index"]): slide for slide in batch_fallback["slides"]
        }
        accepted: dict[int, dict] = {}
        if not batch_was_fallback:
            for batch_slide in batch_slides:
                slide_index = int(batch_slide.get("slide_index", -1))
                expected_template_index = int(
                    fallback_by_index.get(slide_index, {}).get(
                        "template_slide_index", -1
                    )
                )
                returned_template_index = int(
                    batch_slide.get("template_slide_index", -2)
                )
                if (
                    slide_index in expected_indices
                    and slide_index not in accepted
                    and returned_template_index == expected_template_index
                ):
                    accepted[slide_index] = batch_slide
                elif (
                    slide_index in expected_indices
                    and returned_template_index != expected_template_index
                ):
                    logger.warning(
                        "Rejecting LLM template page override for slide %d: "
                        "returned=%d expected=%d",
                        slide_index,
                        returned_template_index,
                        expected_template_index,
                    )

        # JSON repair may return valid JSON containing fewer slides.  Retry
        # every missing slide independently instead of silently shrinking the
        # deck or substituting deterministic copy.
        missing = [index for index in expected_indices if index not in accepted]
        for missing_index in missing:
            outline_slide = next(
                slide for slide in batch_outline_slides
                if int(slide["slide_index"]) == missing_index
            )
            fallback_slide = fallback_by_index[missing_index]
            retry_context = {
                **batch_context,
                "outline": {
                    "meta": context["outline"]["meta"],
                    "slides": [outline_slide],
                },
                "expected_slide_indices": [missing_index],
                "design_plan": {
                    "theme_profile": context["design_plan"].get("theme_profile", {}),
                    "slides": [
                        slide for slide in context["design_plan"].get("slides", [])
                        if int(slide.get("slide_index", -1)) == missing_index
                    ],
                },
            }
            if "template_zones" in retry_context:
                retry_context["template_zones"] = {
                    "color_scheme": context["template_zones"]["color_scheme"],
                    "slides": [
                        slide for slide in context["template_zones"]["slides"]
                        if int(slide.get("index", -1))
                        == int(fallback_slide.get("template_slide_index", -2))
                    ],
                }

            recovered = None
            retry_schema = build_content_mapping_schema(
                batch_fallback["template_id"],
                [fallback_slide],
            )
            for attempt in range(2):
                retry_phase = f"content_mapping_retry_{missing_index}_{attempt + 1}"
                retry_result = llm_client.generate_json(
                    prompt=(
                        enhanced_prompt
                        + "\n\nReturn exactly the one requested slide. Preserve its original "
                          "slide_index; returning a local 0-based index is invalid."
                    ),
                    context=retry_context,
                    system="You are a content editor for PPT generation. Output only valid JSON.",
                    phase=retry_phase,
                    fallback={
                        "template_id": batch_fallback["template_id"],
                        "slides": [],
                    },
                    max_tokens=12000,
                    temperature=0.0,
                    json_schema=retry_schema,
                    schema_name="content_mapping_slide",
                )
                retry_result = normalize_content_mapping_response(retry_result)
                if getattr(llm_client, "was_fallback", lambda _phase: False)(retry_phase):
                    continue
                recovered = next(
                    (
                        slide for slide in retry_result.get("slides", [])
                        if int(slide.get("slide_index", -1)) == missing_index
                    ),
                    None,
                )
                if recovered:
                    recovered.setdefault("fallback_flags", []).append(
                        f"llm_batch_recovered:batch_{batch_start}"
                    )
                    break
            if recovered:
                accepted[missing_index] = recovered
            else:
                fallback_slide["review_status"] = "needs_review"
                fallback_slide.setdefault("fallback_flags", []).extend([
                    f"llm_mapping_fallback:batch_{batch_start}",
                    f"missing_llm_slide:{missing_index}",
                ])
                accepted[missing_index] = fallback_slide

        for index in expected_indices:
            accepted_slide = accepted[index]
            if not any(
                str(flag).startswith("llm_mapping_fallback:")
                for flag in accepted_slide.get("fallback_flags", [])
            ):
                for zone in accepted_slide.get("zones", []):
                    zone.setdefault("source", "llm")
            all_batch_slides.append(accepted_slide)

    # Never allow a repaired JSON response to silently shrink or reorder the
    # deck.  Even deterministic last-resort slides retain their original
    # indices and remain explicitly marked for review.
    expected_all = [int(slide["slide_index"]) for slide in outline_slides]
    actual_all = [int(slide.get("slide_index", -1)) for slide in all_batch_slides]
    if actual_all != expected_all or len(set(actual_all)) != len(actual_all):
        raise ValueError(
            "Content mapping slide cardinality mismatch: "
            f"expected={expected_all}, actual={actual_all}"
        )

    # Assemble and validate the full merged result
    result = {
        "template_id": selected.get("template_id") or selected.get("selected_template_id", "fallback.default"),
        "review_status": "draft",
        "slides": all_batch_slides,
    }
    # Lock structural choices before asking for zone-level repairs. Otherwise
    # an LLM-supplied (but valid) template index can make the repair pass edit
    # zones from the wrong source page.
    _ensure_valid_mapping(
        result, outline, selected, design, source_summary, template_zones
    )
    _repair_mapping_zones_with_llm(
        llm_client, result, outline, design, source_summary, template_zones
    )
    _ensure_valid_mapping(result, outline, selected, design, source_summary, template_zones)
    return result


def _ensure_valid_mapping(
    mapping: dict, outline: dict, selected: dict, design: dict,
    source_summary: dict, template_zones: dict | None = None,
) -> None:
    """Ensure the LLM mapping has all required fields."""
    mapping.setdefault("template_id", selected.get("template_id") or selected.get("selected_template_id", "fallback.default"))
    mapping.setdefault("review_status", "draft")
    mapping.setdefault("assembly_policy", {
        "mode": "text_replace_only",
        "allow_overlay": False,
        "allow_resize": False,
        "allow_font_change": False,
        "allow_position_change": False,
        "require_all_text_zones_actioned": True,
        "require_nonempty_text": True,
        "allow_clear_text": False,
        "match_original_length": True,
        "target_length_ratio": [0.55, 1.35],
        "require_render_validation": True,
    })

    if "slides" not in mapping or not mapping["slides"]:
        fallback = _fallback_mapping(outline, selected, design, source_summary, template_zones)
        mapping["slides"] = fallback["slides"]
        return

    design_by_index = {int(item["slide_index"]): item for item in design.get("slides", [])}
    outline_by_index = {
        int(item.get("slide_index", index)): item
        for index, item in enumerate(outline.get("slides", []))
    }
    outline_position = {index: position for position, index in enumerate(outline_by_index)}
    image_inventory = source_summary.get("image_inventory", [])
    tpl_slides = {}
    tpl_count = 0
    selected_template_indices: list[int] = []
    if template_zones:
        tpl_slides = {s["index"]: s for s in template_zones.get("slides", [])}
        tpl_count = len(tpl_slides)
        selected_template_indices = _select_template_slides(
            outline, template_zones, design
        )
    deterministic_by_slide_index = {
        int(item.get("slide_index", -1)): item
        for item in (
            _fallback_mapping(
                outline, selected, design, source_summary, template_zones
            ).get("slides", [])
            if template_zones
            else []
        )
    }

    mapping["slides"] = sorted(
        mapping["slides"], key=lambda item: int(item.get("slide_index", 10**9))
    )
    for slide in mapping["slides"]:
        slide_index = int(slide.get("slide_index", -1))
        position_index = outline_position.get(slide_index, 0)
        decision = design_by_index.get(slide_index, {})
        # Layout comes from the template page itself — OVERRIDE whatever the LLM said
        expected_template_index = (
            selected_template_indices[position_index]
            if position_index < len(selected_template_indices)
            else position_index % max(tpl_count, 1)
        )
        returned_template_index = slide.get("template_slide_index")
        template_slide_index = expected_template_index
        if (
            tpl_slides
            and returned_template_index is not None
            and int(returned_template_index) != expected_template_index
        ):
            slide.setdefault("fallback_flags", []).append(
                "llm_template_slide_override_rejected:"
                f"{returned_template_index}->{expected_template_index}"
            )
        if template_slide_index not in tpl_slides and tpl_slides:
            slide.setdefault("fallback_flags", []).append(
                f"invalid_template_slide_index:{template_slide_index}"
            )
            template_slide_index = sorted(tpl_slides)[position_index % tpl_count]
        slide["template_slide_index"] = template_slide_index
        tpl_slide = tpl_slides.get(template_slide_index, {})
        layout = tpl_slide.get("layout", "fallback.basic")
        slide["layout"] = layout
        slide["layout_id"] = layout
        slide.setdefault("visual_density", decision.get("visual_density", "medium"))
        slide.setdefault("review_status", "draft")
        slide.setdefault("source_refs", [])
        slide.setdefault("fallback_flags", [])
        slide.setdefault("template_selection", {
            "score": None,
            "baked_text_risk": _assess_baked_text_risk(tpl_slide) if tpl_slide else {"level": "none"},
            "reason": "template page selected before zone mapping",
        })

        # ── Validate zone_ids (must exist in template all_zones) ──
        outline_slide = outline_by_index.get(slide_index, {})
        if tpl_slide:
            valid_zone_ids = {z.get("zone_id", "") for z in tpl_slide.get("all_zones", [])}
            if valid_zone_ids:
                mapped_zone_ids = [
                    zone.get("zone_id", "")
                    for zone in slide.get("zones", [])
                    if zone.get("zone_id")
                ]
                invalid_zone_ids = [
                    zone_id for zone_id in mapped_zone_ids
                    if zone_id not in valid_zone_ids
                ]
                # A majority-invalid zone set means the model filled another
                # template page while still returning a valid page index.
                # Individual bad IDs can be rejected in place, but a crossed
                # page must be reset as one unit before micro repair.
                if (
                    len(invalid_zone_ids) >= 2
                    and len(invalid_zone_ids) * 2 >= max(1, len(mapped_zone_ids))
                ):
                    prior_flags = list(slide.get("fallback_flags", []))
                    fallback_slide = deterministic_by_slide_index.get(slide_index, {})
                    slide["zones"] = [
                        dict(zone) for zone in fallback_slide.get("zones", [])
                    ]
                    slide["fallback_flags"] = list(dict.fromkeys([
                        *prior_flags,
                        "cross_template_zone_set_rejected:"
                        f"{len(invalid_zone_ids)}/{len(mapped_zone_ids)}",
                        *fallback_slide.get("fallback_flags", []),
                    ]))
                rejected_ids = [
                    zone.get("zone_id", "")
                    for zone in slide.get("zones", [])
                    if zone.get("zone_id", "") not in valid_zone_ids
                ]
                if rejected_ids:
                    slide["zones"] = [
                        zone for zone in slide.get("zones", [])
                        if zone.get("zone_id", "") in valid_zone_ids
                    ]
                    slide["fallback_flags"] = [
                        flag for flag in slide.get("fallback_flags", [])
                        if not str(flag).startswith("invalid_zone_id:")
                    ]
                    slide["fallback_flags"].append(
                        f"invalid_zones_discarded:{len(rejected_ids)}"
                    )

        # ── Safety-net: copy real positions from template (AI chose zone_id, we double-check) ──
        if tpl_slide:
            all_tpl_zones = tpl_slide.get("all_zones", tpl_slide.get("text_zones", []))
            if all_tpl_zones:
                slide["zones"] = _lock_zone_positions(
                    slide.get("zones", []),
                    all_tpl_zones,
                    [],  # image zones included in all_zones
                )

        # ── Preserve template formatting on matched zones ──
        if tpl_slide:
            slide["zones"] = _inject_formatting(slide.get("zones", []), tpl_slide)

        # ── Safety-net: truncate content that exceeds zone capacity ──
        if tpl_slide:
            slide["zones"] = _truncate_overflow(slide.get("zones", []), tpl_slide)

        # Carry template_image through for downstream img2img
        if tpl_slide.get("template_image"):
            slide.setdefault("template_image", tpl_slide["template_image"])

        if "zones" not in slide or not slide["zones"]:
            image = image_inventory[position_index] if position_index < len(image_inventory) else None

            if tpl_slide:
                # Use template zone positions with semantic label matching
                slide["zones"] = [z.to_dict() if hasattr(z, 'to_dict') else z
                                  for z in _build_zones_for_slide(
                                      outline_slide, tpl_slide, image, slide_index
                                  )]
            else:
                slide["zones"] = [
                    {"zone_id": "title", "type": "title", "position": [0.08, 0.08, 0.84, 0.16],
                     "editable": True, "content": outline_slide.get("title", f"Slide {slide_index+1}"),
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
                zone.setdefault(
                    "action",
                    "replace_text" if zone.get("content") is not None else "preserve",
                )
                zone.setdefault("placement_reason", "")
                zone.setdefault("source_block_ids", [])
                zone.setdefault("transformation", "none")
                if zone.get("formatting") is None:
                    zone["formatting"] = None

        if tpl_slide:
            image = image_inventory[position_index] if position_index < len(image_inventory) else None
            deterministic_zones = _build_zones_for_slide(
                outline_slide, tpl_slide, image, slide_index
            )
            deterministic_by_id = {zone.zone_id: zone.to_dict() for zone in deterministic_zones}
            template_text_by_id = {
                zone.get("zone_id", ""): zone
                for zone in tpl_slide.get("text_zones", []) if zone.get("zone_id")
            }
            # The LLM has already received two bounded repair opportunities.
            # Deterministic copy is now a last resort and is used only when a
            # complete source fragment genuinely fits the zone.
            for zone_index, zone in enumerate(slide.get("zones", [])):
                zid = zone.get("zone_id", "")
                template_zone = template_text_by_id.get(zid)
                fallback_zone = deterministic_by_id.get(zid)
                if not template_zone or not fallback_zone:
                    continue
                unsafe = bool(_zone_repair_reasons(zone, template_zone))
                if unsafe:
                    fallback_reasons = _zone_repair_reasons(fallback_zone, template_zone)
                    if not fallback_reasons:
                        slide["zones"][zone_index] = fallback_zone
                        slide["fallback_flags"].append(
                            f"deterministic_last_resort:{zid}"
                        )
                    else:
                        zone["fit_status"] = "overflow"
                        slide["fallback_flags"].append(
                            f"llm_constraint_unresolved:{zid}"
                        )

            existing = {z.get("zone_id") for z in slide.get("zones", [])}
            for template_zone in tpl_slide.get("text_zones", []):
                zid = template_zone.get("zone_id", "")
                if zid and zid not in existing:
                    replacement = deterministic_by_id.get(zid)
                    if replacement:
                        slide["zones"].append(replacement)
                        if _zone_repair_reasons(replacement, template_zone):
                            slide["fallback_flags"].append(
                                f"llm_constraint_unresolved:{zid}"
                            )
                        else:
                            slide["fallback_flags"].append(
                                f"deterministic_last_resort_missing:{zid}"
                            )
                    else:
                        slide["fallback_flags"].append(f"missing_zone_unresolved:{zid}")

            # Re-evaluate capacity after deterministic repairs; repairs are not
            # allowed to erase a genuine overflow finding.
            _synchronize_layered_text_zones(slide.get("zones", []), tpl_slide)
            slide["zones"] = _truncate_overflow(slide.get("zones", []), tpl_slide)
            _reconcile_zone_repair_flags(slide, tpl_slide)
            _ensure_generated_content_image_zone(
                slide,
                tpl_slide,
                decision,
                outline_slide,
                image_inventory,
            )

        hard_issue_prefixes = (
            "length_mismatch:", "clear_text_forbidden:",
            "missing_replacement_text:", "content_eligibility_violation:",
            "overflow:", "empty_preserve_forbidden:",
        )
        slide["fallback_flags"] = [
            flag for flag in slide.get("fallback_flags", [])
            if not str(flag).startswith(hard_issue_prefixes)
        ]
        mapping_issues = _validate_mapping_plan(slide, tpl_slide)
        if mapping_issues:
            slide["fallback_flags"].extend(
                issue for issue in mapping_issues if issue not in slide["fallback_flags"]
            )
            slide["review_status"] = "needs_review"

    mapping["review_status"] = aggregate_review_status(mapping["slides"])


def _validate_mapping_plan(slide: dict, tpl_slide: dict) -> list[str]:
    """Return deterministic issues that an Agent is not allowed to waive."""
    issues: list[str] = []
    zones = slide.get("zones", [])
    zone_ids = [zone.get("zone_id", "") for zone in zones]

    duplicates = sorted({zid for zid in zone_ids if zid and zone_ids.count(zid) > 1})
    issues.extend(f"duplicate_zone:{zid}" for zid in duplicates)

    valid_by_id = {
        zone.get("zone_id", ""): zone for zone in tpl_slide.get("all_zones", [])
        if zone.get("zone_id")
    }
    required_text_ids = {
        zone.get("zone_id", "") for zone in tpl_slide.get("text_zones", [])
        if zone.get("zone_id") and zone.get("editable", True)
    }
    actual_ids = {zid for zid in zone_ids if zid}
    issues.extend(f"unactioned_zone:{zid}" for zid in sorted(required_text_ids - actual_ids))

    allowed_actions = {"replace_text", "clear_text", "preserve", "replace_image"}
    for zone in zones:
        zid = zone.get("zone_id", "")
        action = zone.get("action", "")
        content = zone.get("content")
        template_zone = valid_by_id.get(zid, {})
        is_required_text = zid in required_text_ids
        if zid.startswith("_overlay_"):
            issues.append(f"overlay_forbidden:{zid}")
        if valid_by_id and zid not in valid_by_id:
            issues.append(f"invalid_zone_id:{zid}")
        if action not in allowed_actions:
            issues.append(f"invalid_action:{zid}")
        if is_required_text and action == "clear_text":
            issues.append(f"clear_text_forbidden:{zid}")
        if action == "replace_text" and not _clean_mapping_text(
            _content_text(content)
        ):
            issues.append(f"missing_replacement_text:{zid}")
        if is_required_text and action == "preserve" and not _clean_mapping_text(
            template_zone.get("original_text", template_zone.get("text", ""))
        ):
            issues.append(f"empty_preserve_forbidden:{zid}")
        if is_required_text and action == "preserve" and not _fixed_template_text(template_zone):
            issues.append(f"template_sample_preserved:{zid}")
        if action == "replace_image" and not (
            zone.get("image_ref") or zone.get("generated_image") or zone.get("image_path")
        ):
            issues.append(f"missing_replacement_image:{zid}")
        if zone.get("fit_status") == "overflow":
            issues.append(f"overflow:{zid}")
        if zone.get("transformation", "none") != "none" and not zone.get("source_block_ids"):
            issues.append(f"untraceable_rewrite:{zid}")
        eligibility = template_zone.get("content_eligibility")
        if action == "replace_text" and eligibility == "forbidden":
            issues.append(f"content_eligibility_violation:{zid}")
        if action == "replace_text" and eligibility in ("short_label", "decorative"):
            rendered = "\n".join(content) if isinstance(content, list) else str(content or "")
            if len(rendered) > int(template_zone.get("max_chars_hint") or 12):
                issues.append(f"content_eligibility_violation:{zid}")
        if action == "replace_text" and content not in (None, "", []):
            lower, upper = _zone_target_range(template_zone)
            mapped_length = _visible_char_count(content)
            if mapped_length < lower or mapped_length > upper:
                issues.append(f"length_mismatch:{zid}")
            if _clean_mapping_text(content).lower() in {"u", "v", "w", "•", "●"}:
                issues.append(f"orphan_bullet_or_glyph:{zid}")

    return issues


def _lock_zone_positions(
    llm_zones: list[dict],
    tpl_text_zones: list[dict],
    tpl_image_zones: list[dict],
) -> list[dict]:
    """Safety-net: copy real template positions by zone_id.

    The AI already chose the right zone_id from context. This function just
    double-checks: for every zone_id the AI used, copy the REAL position
    from the template.  No type-based re-matching — the AI's zone_id choice is
    authoritative.
    """
    # Build zone_id → position lookup
    tpl_positions: dict[str, list[float]] = {}
    for tz in tpl_text_zones:
        if tz.get("zone_id"):
            tpl_positions[tz["zone_id"]] = tz.get("position", [0.1, 0.1, 0.8, 0.8])
    for iz in tpl_image_zones:
        if iz.get("zone_id"):
            tpl_positions[iz["zone_id"]] = iz.get("position", [0.1, 0.1, 0.8, 0.8])

    for zone in llm_zones:
        zid = zone.get("zone_id", "")
        real_pos = tpl_positions.get(zid)
        if real_pos is not None:
            zone["position"] = real_pos

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


def _truncate_overflow(zones: list[dict], tpl_slide: dict) -> list[dict]:
    """Audit capacity without mutating visible text.

    Kept under its historical name for compatibility.  Previous versions
    sliced strings and then labelled the fragments as ``fits``.  That hid
    failed mappings and produced copy such as ``WebSo`` and ``320m``.  The
    function now only marks overflow; an LLM repair pass must rewrite the
    complete phrase, or the slide remains blocked for review.
    """
    all_zones = {z.get("zone_id", ""): z for z in tpl_slide.get("all_zones", [])}

    for zone in zones:
        zid = zone.get("zone_id", "")
        tpl_zone = all_zones.get(zid, {})
        pos = tpl_zone.get("position", [0, 0, 0, 0])
        if len(pos) < 4:
            continue
        w, h = pos[2], pos[3]
        fmt = zone.get("formatting", {}) or {}
        font_pt = fmt.get("font_size_pt", 14) or 14
        max_lines = max(1, int(h * 900 / max(int(font_pt), 10)))
        chars_per_line = max(5, int(w * 650 / max(int(font_pt), 10)))

        content = zone.get("content")
        zone_type = zone.get("type", "")
        target_min, target_max = _zone_target_range(tpl_zone)
        visible_length = _visible_char_count(content)
        # Target length is a copy-quality constraint, not proof of geometric
        # overflow. A five-character phrase in a 24-character box is an
        # underfill/length mismatch, but it still physically fits.
        overflow = False

        if zone_type in ("title", "subtitle") and isinstance(content, str):
            overflow = overflow or visible_length > chars_per_line
        elif zone_type in ("bullets", "body") and isinstance(content, list):
            overflow = overflow or len(content) > max_lines or any(
                _visible_char_count(item) > chars_per_line for item in content
            )
        elif zone_type in ("bullets", "body", "footer") and isinstance(content, str):
            overflow = overflow or visible_length > chars_per_line * max_lines

        if overflow:
            zone["fit_status"] = "overflow"
            zone.setdefault("_warning", "capacity constraint requires semantic rewrite")
        elif zone.get("action") == "replace_text":
            zone["fit_status"] = "fits"
            zone.pop("_warning", None)

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


def _mapping_generation_stats(payload: dict) -> dict:
    """Measure how much final visible copy still comes from an LLM decision."""
    text_zones = [
        zone
        for slide in payload.get("slides", [])
        for zone in slide.get("zones", [])
        if zone.get("action") == "replace_text"
        and zone.get("type") in {"title", "subtitle", "bullets", "body", "footer", "decorative"}
    ]
    llm_zones = [zone for zone in text_zones if zone.get("source") == "llm"]
    deterministic_zones = [zone for zone in text_zones if zone.get("source") != "llm"]
    total = len(text_zones)
    ratio = len(llm_zones) / total if total else 0.0
    return {
        "replace_text_zones": total,
        "llm_text_zones": len(llm_zones),
        "deterministic_text_zones": len(deterministic_zones),
        "llm_text_ratio": round(ratio, 4),
        "minimum_llm_text_ratio": 0.85,
        "passed": bool(total) and ratio >= 0.85,
    }


def _mapping_llm_repair_target_count(
    payload: dict,
    template_zones: dict | None,
) -> int:
    """Count cached zones that need a new LLM-authored decision."""
    if not template_zones:
        return 0
    template_by_index = {
        int(slide.get("index", index)): slide
        for index, slide in enumerate(template_zones.get("slides", []))
    }
    count = 0
    for slide in payload.get("slides", []):
        template_index = int(
            slide.get("template_slide_index", slide.get("slide_index", -1))
        )
        template_slide = template_by_index.get(template_index, {})
        mapped_by_id = {
            zone.get("zone_id", ""): zone for zone in slide.get("zones", [])
            if zone.get("zone_id")
        }
        for template_zone in template_slide.get("text_zones", []):
            zone_id = template_zone.get("zone_id", "")
            if (
                zone_id
                and template_zone.get("editable", True)
                and _llm_zone_repair_reasons(
                    mapped_by_id.get(zone_id), template_zone
                )
            ):
                count += 1
    return count


def _build_mapping_diagnostics(payload: dict, template_zones: dict | None) -> dict:
    template_by_index = {
        int(slide.get("index", index)): slide
        for index, slide in enumerate((template_zones or {}).get("slides", []))
    }
    slide_reports: list[dict] = []
    total_issues = 0
    for slide in payload.get("slides", []):
        template_index = int(slide.get("template_slide_index", slide.get("slide_index", 0)))
        template_slide = template_by_index.get(template_index, {})
        template_by_id = {
            zone.get("zone_id", ""): zone
            for zone in template_slide.get("all_zones", []) if zone.get("zone_id")
        }
        issues = _validate_mapping_plan(slide, template_slide)
        total_issues += len(issues)
        zones: list[dict] = []
        for zone in slide.get("zones", []):
            template_zone = template_by_id.get(zone.get("zone_id", ""), {})
            # Legacy metadata did not persist an explicit editable flag for
            # native text zones. Only an explicit false is non-editable.
            if template_zone.get("editable") is False:
                continue
            target_min, target_max = _zone_target_range(template_zone)
            zones.append({
                "zone_id": zone.get("zone_id", ""),
                "action": zone.get("action", ""),
                "semantic_role": template_zone.get("semantic_role", ""),
                "content_eligibility": template_zone.get("content_eligibility", ""),
                "original_text": template_zone.get("original_text", template_zone.get("text", "")),
                "mapped_text": _content_text(zone.get("content")),
                "original_char_count": _visible_char_count(
                    template_zone.get("original_text", template_zone.get("text", ""))
                ),
                "mapped_char_count": _visible_char_count(zone.get("content")),
                "target_char_range": [target_min, target_max],
                "fit_status": zone.get("fit_status", "unknown"),
                "formatting_fingerprint": template_zone.get("formatting_fingerprint", ""),
                "placement_reason": zone.get("placement_reason", ""),
            })
        slide_reports.append({
            "slide_index": slide.get("slide_index", 0),
            "template_slide_index": template_index,
            "status": "passed" if not issues else "needs_review",
            "issues": issues,
            "zones": zones,
        })
    generation_stats = _mapping_generation_stats(payload)
    if not generation_stats["passed"]:
        total_issues += 1
    return {
        "status": "passed" if total_issues == 0 else "needs_review",
        "policy": {
            "fill_every_editable_text_zone": True,
            "clear_text_allowed": False,
            "preserve_formatting": True,
            "target_length_ratio": [0.55, 1.35],
        },
        "issue_count": total_issues,
        "generation_stats": generation_stats,
        "slides": slide_reports,
    }


def _archive_resolved_fallback_flags(payload: dict, diagnostics: dict) -> None:
    """Move historical recovery markers out of the active failure channel."""
    if diagnostics.get("issue_count") != 0:
        return
    for slide in payload.get("slides", []):
        flags = list(slide.get("fallback_flags", []))
        if not flags:
            continue
        slide["mapping_history_flags"] = list(dict.fromkeys([
            *slide.get("mapping_history_flags", []),
            *flags,
        ]))
        slide["fallback_flags"] = []


def run(workspace: JobWorkspace, force: bool = False, llm_client=None) -> Path:
    output = workspace.artifact_path("slide_contents")
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

    cached_payload: dict | None = None
    cached_target_count = 0
    cached_was_approved = False
    if output.exists() and not force:
        if llm_client is None or template_zones is None:
            return output
        cached_payload = load_artifact(workspace, "slide_contents")
        cached_was_approved = cached_payload.get("review_status") == "approved"
        cached_flags_changed = False
        cached_template_by_index = {
            int(slide.get("index", index)): slide
            for index, slide in enumerate(template_zones.get("slides", []))
        }
        for cached_slide in cached_payload.get("slides", []):
            template_index = int(cached_slide.get(
                "template_slide_index", cached_slide.get("slide_index", -1)
            ))
            cached_template_slide = cached_template_by_index.get(template_index)
            if not cached_template_slide:
                continue
            before = list(cached_slide.get("fallback_flags", []))
            _reconcile_zone_repair_flags(cached_slide, cached_template_slide)
            cached_flags_changed = (
                cached_flags_changed
                or before != cached_slide.get("fallback_flags", [])
            )
        cached_target_count = _mapping_llm_repair_target_count(
            cached_payload, template_zones
        )
        if cached_target_count == 0:
            if cached_flags_changed:
                write_artifact(workspace, "slide_contents", cached_payload)
                write_artifact(
                    workspace,
                    "mapping_diagnostics",
                    _build_mapping_diagnostics(cached_payload, template_zones),
                )
            return output
        if cached_was_approved:
            logger.warning(
                "Approved slide_contents still has %d invalid text zone(s); "
                "running targeted repair before assembly.",
                cached_target_count,
            )

    if cached_payload is not None:
        logger.info(
            "Repairing %d cached content-mapping zones with micro LLM calls",
            cached_target_count,
        )
        payload = cached_payload
        _ensure_valid_mapping(
            payload, outline, selected, design, source_summary, template_zones
        )
        _repair_mapping_zones_with_llm(
            llm_client, payload, outline, design, source_summary, template_zones
        )
        _ensure_valid_mapping(
            payload, outline, selected, design, source_summary, template_zones
        )
        remaining_targets = _mapping_llm_repair_target_count(
            payload, template_zones
        )
        payload["mapping_mode"] = "llm_first"
        payload["targeted_repair"] = {
            "mode": "cached_zone_micro_repair",
            "initial_target_zones": cached_target_count,
            "remaining_target_zones": remaining_targets,
        }
        # An approved artifact may still contain machine-detectable fit issues.
        # Repair those automatically, but preserve the user's approval so an
        # irreducible micro-copy constraint cannot dead-end the whole job.
        payload["review_status"] = (
            "approved"
            if cached_was_approved
            else ("draft" if remaining_targets == 0 else "needs_review")
        )
    elif llm_client is not None:
        logger.info("Using LLM for content mapping")
        payload = _llm_mapping(llm_client, outline, selected, design, source_summary, template_zones)
        payload["mapping_mode"] = "llm_first"
    else:
        logger.info("No LLM client, using fallback content mapping")
        payload = _fallback_mapping(outline, selected, design, source_summary, template_zones)
        payload["mapping_mode"] = "deterministic_fallback"
        _ensure_valid_mapping(
            payload, outline, selected, design, source_summary, template_zones
        )

    generation_stats = _mapping_generation_stats(payload)
    payload["generation_stats"] = generation_stats
    if (
        payload["mapping_mode"] == "llm_first"
        and not generation_stats["passed"]
        and not cached_was_approved
    ):
        payload["review_status"] = "needs_review"

    diagnostics = _build_mapping_diagnostics(payload, template_zones)
    _archive_resolved_fallback_flags(payload, diagnostics)
    output_path = write_artifact(workspace, "slide_contents", payload)
    write_artifact(workspace, "mapping_diagnostics", diagnostics)
    return output_path
