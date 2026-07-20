"""Template metadata model.

Provides ``default_template_meta()`` for the fallback template, and
``build_template_meta_from_ingest()`` for building meta from ingested
template data (with real zones from OCR/shape analysis).
"""

from __future__ import annotations

DEFAULT_ZONES = [
    {"zone_id": "title", "type": "title", "position": [0.08, 0.08, 0.84, 0.16], "font_size": 34},
    {"zone_id": "body", "type": "body", "position": [0.10, 0.28, 0.52, 0.54], "font_size": 20},
    {"zone_id": "image", "type": "image", "position": [0.68, 0.30, 0.24, 0.40], "font_size": 14},
]


def default_template_meta(slide_count: int = 8) -> dict:
    """Fallback template meta — used when no real template is available."""
    return {
        "template_id": "fallback.default",
        "domain_tags": ["general", "business"],
        "audience_tags": ["stakeholders", "reviewers"],
        "tone_tags": ["professional", "clear"],
        "color_scheme": {
            "primary": "#1F4E79",
            "secondary": "#70AD47",
            "accent": "#F4B183",
            "background": "#FFFFFF",
        },
        "style": "Clean professional fallback template with editable text zones.",
        "slide_count": slide_count,
        "preview_paths": [],
        "retrieval_text": "general business project report professional clean",
        "slides": [
            {
                "index": i,
                "layout": "fallback.basic" if i else "cover.hero",
                "visual_density": "medium",
                "zones": DEFAULT_ZONES,
            }
            for i in range(slide_count)
        ],
    }


def build_template_meta_from_ingest(ingest_meta: dict) -> dict:
    """Convert ingested template meta into the pipeline's template_meta format.

    The ingest meta comes from ``templates/<dir>/meta.json`` and has
    real zone data from OCR and shape analysis.  This function maps it
    to the schema expected by downstream phases (content_mapping,
    design_planning, ppt_assembly).
    """
    slides = []
    for slide in ingest_meta.get("slides", []):
        # Classify zones for downstream use
        text_zones = []
        image_zones = []
        for z in slide.get("zones", []):
            ztype = z.get("type", "decoration")
            if z.get("editable") or ztype in ("title", "subtitle", "body", "footer"):
                text_zones.append(z)
            elif ztype == "image":
                image_zones.append(z)

        slides.append({
            "index": slide["index"],
            "layout": _infer_layout(slide),
            "visual_density": _infer_density(slide),
            "zones": slide.get("zones", []),
            "text_zones": text_zones,
            "image_zones": image_zones,
            "image_path": slide.get("image_path"),
            "ocr_text": slide.get("ocr_text", ""),
            "baked_text_regions": slide.get("baked_text_regions", []),
        })

    return {
        "meta_schema_version": ingest_meta.get("meta_schema_version", "1.0"),
        "parser_strategy": ingest_meta.get("parser_strategy", "legacy"),
        "template_sha256": ingest_meta.get("template_sha256", ""),
        "template_id": ingest_meta.get("template_id", "unknown"),
        "domain_tags": ingest_meta.get("domain_tags", []),
        "audience_tags": ingest_meta.get("audience_tags", ["general"]),
        "tone_tags": ingest_meta.get("tone_tags", ["professional"]),
        "color_scheme": ingest_meta.get("color_scheme", "blue"),
        "theme_colors": ingest_meta.get("theme_colors", {}),
        "style": ingest_meta.get("style", ""),
        "slide_count": ingest_meta.get("slide_count", len(slides)),
        "retrieval_text": ingest_meta.get("retrieval_text", ""),
        "slides": slides,
    }


def _infer_layout(slide: dict) -> str:
    """Infer layout name from zone structure."""
    zones = slide.get("zones", [])
    types = {z.get("type") for z in zones}

    if slide.get("index", 0) == 0:
        return "cover.hero"

    if "image" in types and ("body" in types or "title" in types):
        return "content.split"
    if "body" in types and "title" in types:
        return "content.text"
    if "image" in types:
        return "visual.full"

    return "fallback.basic"


def _infer_density(slide: dict) -> str:
    """Infer visual density from zone count and types."""
    zones = slide.get("zones", [])
    if len(zones) >= 6:
        return "high"
    if len(zones) >= 3:
        return "medium"
    return "low"
