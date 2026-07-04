"""Image layer analysis for Phase 5 (design planning).

Reads slide_design_plan.json and slide_contents.json (if available),
then extracts per-slide visual layers: background, text, image, shape,
and chart.

Each slide's layer analysis is written to
``{workspace}/layer_analysis/slide_{idx}.json`` and an overall
``layer_analysis_report.json`` artifact is produced.

If slide_contents.json is not available, the analysis falls back to
design-plan-only mode, producing a best-effort layer breakdown with
warnings.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ppt_agent.coordinator.phase_state import load_artifact, write_artifact
from ppt_agent.models.artifacts import JobWorkspace, atomic_write_json

logger = logging.getLogger(__name__)

# Zone types from slide_contents that map to the text layer
_TEXT_ZONE_TYPES = {"title", "subtitle", "bullets", "body", "caption", "footer"}

# Zone types that map to the image layer
_IMAGE_ZONE_TYPES = {"image", "visual", "photo", "screenshot"}

# Zone types that map to the chart layer
_CHART_ZONE_TYPES = {"chart", "data", "table", "graph"}

# Visual strategies that imply chart content
_CHART_STRATEGIES = {"chart"}

# Visual strategies that imply generated image content
_GENERATED_IMAGE_STRATEGIES = {"generated_background", "generated_region"}


# -- Per-layer extraction helpers ------------------------------------------


def _extract_background(theme_profile: dict, slide_plan: dict) -> dict:
    """Extract the background layer from theme tokens and slide plan."""
    color_tokens = theme_profile.get("color_tokens", {})
    bg_color = color_tokens.get("background", "#FFFFFF")

    visual_strategy = slide_plan.get("visual_strategy", "placeholder")
    if visual_strategy == "generated_background":
        return {
            "type": "gradient",
            "color": bg_color,
            "gradient_to": color_tokens.get("primary", "#1F4E79"),
        }

    return {"type": "solid", "color": bg_color}


def _font_size_for_zone_type(zone_type: str) -> int:
    """Determine a reasonable font size based on zone type."""
    if zone_type == "title":
        return 36
    if zone_type == "subtitle":
        return 24
    if zone_type == "caption":
        return 14
    if zone_type == "footer":
        return 10
    # body / bullets
    return 18


def _extract_text_layer(zones: list[dict]) -> list[dict]:
    """Extract text elements from slide zones."""
    text_elements: list[dict] = []

    for zone in zones:
        zone_type = zone.get("type", "")
        if zone_type not in _TEXT_ZONE_TYPES:
            continue

        content = zone.get("content")
        if content is None:
            continue

        # Normalize list content to a single string
        if isinstance(content, list):
            content_str = "\n".join(str(item) for item in content)
        else:
            content_str = str(content)

        text_elements.append({
            "content": content_str,
            "position": zone.get("position", [0.0, 0.0, 1.0, 1.0]),
            "font_size": _font_size_for_zone_type(zone_type),
            "role": zone_type,
            "zone_id": zone.get("zone_id", ""),
            "editable": zone.get("editable", True),
        })

    return text_elements


def _extract_image_layer(slide_plan: dict, zones: list[dict]) -> list[dict]:
    """Extract image elements from slide zones and visual strategy."""
    images: list[dict] = []

    for zone in zones:
        zone_type = zone.get("type", "")
        if zone_type not in _IMAGE_ZONE_TYPES:
            continue

        source = zone.get("source", "placeholder")
        entry: dict = {
            "prompt": zone.get("image_prompt", ""),
            "position": zone.get("position", [0.0, 0.0, 1.0, 1.0]),
            "source": source,
            "zone_id": zone.get("zone_id", ""),
        }
        if zone.get("image_ref"):
            entry["image_ref"] = zone["image_ref"]
        images.append(entry)

    # If the visual strategy implies generated content but no image zone
    # exists, add an implicit generated-image entry.
    visual_strategy = slide_plan.get("visual_strategy", "placeholder")
    if not images and visual_strategy in _GENERATED_IMAGE_STRATEGIES:
        images.append({
            "prompt": visual_strategy,
            "position": [0.0, 0.0, 1.0, 1.0],
            "source": "generated",
        })

    return images


def _estimate_block_position(block: dict, layout_id: str) -> list[float]:
    """Estimate a block's position based on its type and the slide layout."""
    block_type = block.get("block_type", "")

    if block_type == "card":
        return [0.08, 0.30, 0.26, 0.30]
    if block_type == "container":
        return [0.05, 0.25, 0.90, 0.65]

    return [0.10, 0.25, 0.80, 0.60]


def _extract_shape_layer(slide_plan: dict, theme_profile: dict) -> list[dict]:
    """Extract decorative shapes from block plan and theme tokens."""
    shapes: list[dict] = []
    color_tokens = theme_profile.get("color_tokens", {})
    shape_tokens = theme_profile.get("shape_tokens", {})
    border_radius = shape_tokens.get("border_radius", 0.02)

    layout_id = slide_plan.get("layout_id", "fallback.basic")
    block_plan = slide_plan.get("block_plan", [])

    for block in block_plan:
        block_type = block.get("block_type", "")

        if block_type in ("card", "container", "panel"):
            shapes.append({
                "shape_type": "roundRect",
                "position": _estimate_block_position(block, layout_id),
                "fill": color_tokens.get("accent", "#EEF2F6"),
                "border_radius": border_radius,
                "block_type": block_type,
            })
        elif block_type == "divider":
            shapes.append({
                "shape_type": "line",
                "position": [0.08, 0.24, 0.84, 0.005],
                "fill": color_tokens.get("muted", "#D1D5DB"),
                "block_type": block_type,
            })
        elif block_type == "accent_bar":
            shapes.append({
                "shape_type": "rect",
                "position": [0.0, 0.0, 0.04, 1.0],
                "fill": color_tokens.get("primary", "#1F4E79"),
                "block_type": block_type,
            })

    # Add layout-specific decorative shapes
    if layout_id == "cover.hero":
        shapes.append({
            "shape_type": "rect",
            "position": [0.0, 0.85, 1.0, 0.15],
            "fill": color_tokens.get("primary", "#1F4E79"),
            "block_type": "decorative_band",
        })
    elif layout_id == "section.divider":
        shapes.append({
            "shape_type": "roundRect",
            "position": [0.35, 0.45, 0.30, 0.10],
            "fill": color_tokens.get("accent", "#F4B183"),
            "border_radius": border_radius,
            "block_type": "decorative_block",
        })

    return shapes


def _extract_chart_layer(slide_plan: dict, zones: list[dict]) -> list[dict]:
    """Extract chart/data visualization elements from zones and visual strategy."""
    charts: list[dict] = []

    for zone in zones:
        zone_type = zone.get("type", "")
        if zone_type not in _CHART_ZONE_TYPES:
            continue

        charts.append({
            "chart_type": zone.get("chart_type", "bar"),
            "position": zone.get("position", [0.0, 0.0, 1.0, 1.0]),
            "data_source": zone.get("content"),
            "zone_id": zone.get("zone_id", ""),
        })

    visual_strategy = slide_plan.get("visual_strategy", "")
    if not charts and visual_strategy in _CHART_STRATEGIES:
        charts.append({
            "chart_type": "bar",
            "position": [0.35, 0.30, 0.55, 0.50],
            "data_source": None,
        })

    return charts


def _determine_z_order(
    text: list,
    image: list,
    shape: list,
    chart: list,
) -> list[str]:
    """Determine the z-order of layers (bottom to top)."""
    z_order: list[str] = ["background"]
    if shape:
        z_order.append("shape")
    if image:
        z_order.append("image")
    if chart:
        z_order.append("chart")
    if text:
        z_order.append("text")
    return z_order


def _analyze_slide(
    slide_index: int,
    slide_plan: dict,
    theme_profile: dict,
    zones: list[dict],
) -> dict:
    """Analyze a single slide and produce its layer breakdown."""
    background = _extract_background(theme_profile, slide_plan)
    text = _extract_text_layer(zones)
    image = _extract_image_layer(slide_plan, zones)
    shape = _extract_shape_layer(slide_plan, theme_profile)
    chart = _extract_chart_layer(slide_plan, zones)
    z_order = _determine_z_order(text, image, shape, chart)

    return {
        "slide_index": slide_index,
        "layers": {
            "background": background,
            "text": text,
            "image": image,
            "shape": shape,
            "chart": chart,
        },
        "z_order": z_order,
    }


# -- Public entry point ----------------------------------------------------


def run(workspace: JobWorkspace, force: bool = False) -> Path:
    """Run layer analysis on the slide design plan.

    Reads ``slide_design_plan.json`` (required) and ``slide_contents.json``
    (optional — provides zone details), then writes per-slide layer analysis
    files to ``{workspace}/layer_analysis/slide_{idx}.json`` and an overall
    ``layer_analysis_report.json`` artifact.

    Args:
        workspace: The job workspace.
        force: If True, re-run even if outputs already exist.

    Returns:
        Path to the ``layer_analysis_report.json`` artifact.
    """
    report_path = workspace.artifact_path("layer_analysis_report")

    if report_path.exists() and not force:
        return report_path

    # Load the design plan (required)
    design_plan = load_artifact(workspace, "slide_design_plan")

    # Load slide contents (optional — provides zone details)
    slide_contents = None
    try:
        slide_contents = load_artifact(workspace, "slide_contents")
    except (FileNotFoundError, Exception):
        logger.info(
            "slide_contents.json not found; layer analysis will use design plan only"
        )

    theme_profile = design_plan.get("theme_profile", {})
    slides_plan = design_plan.get("slides", [])

    # Build zone lookup from slide_contents
    zones_by_index: dict[int, list[dict]] = {}
    if slide_contents:
        for slide in slide_contents.get("slides", []):
            idx = slide.get("slide_index", 0)
            zones_by_index[idx] = slide.get("zones", [])

    layer_dir = workspace.root / "layer_analysis"
    layer_dir.mkdir(parents=True, exist_ok=True)

    slide_analyses: list[dict] = []
    for slide_plan in slides_plan:
        slide_index = slide_plan.get("slide_index", 0)
        zones = zones_by_index.get(slide_index, [])

        analysis = _analyze_slide(slide_index, slide_plan, theme_profile, zones)

        # Write per-slide file
        slide_file = layer_dir / f"slide_{slide_index}.json"
        atomic_write_json(slide_file, analysis)
        slide_analyses.append(analysis)

    # Build and write the overall report
    report = {
        "job_id": workspace.root.name,
        "total_slides": len(slide_analyses),
        "slides": [
            {
                "slide_index": a["slide_index"],
                "layer_counts": {
                    "text": len(a["layers"]["text"]),
                    "image": len(a["layers"]["image"]),
                    "shape": len(a["layers"]["shape"]),
                    "chart": len(a["layers"]["chart"]),
                },
                "z_order": a["z_order"],
                "layers_file": f"layer_analysis/slide_{a['slide_index']}.json",
            }
            for a in slide_analyses
        ],
        "fallback_used": slide_contents is None,
        "warnings": (
            []
            if slide_contents is not None
            else [
                "slide_contents.json not available; "
                "layer analysis based on design plan only"
            ]
        ),
    }

    return write_artifact(workspace, "layer_analysis_report", report)
