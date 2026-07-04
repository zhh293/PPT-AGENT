"""Template matching worker — select the best template for the project.

Reads the outline to understand domain/audience/tone, searches the
template index via BM25+TF-IDF, and produces:
    - selected_template.json  (which template was chosen and why)
    - template_meta.json      (full per-slide zone structure for downstream)
    - template_zones.json     (per-slide zone + image path mapping)
"""

from __future__ import annotations

import logging
from pathlib import Path

from ppt_agent.coordinator.phase_state import load_artifact, write_artifact
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.models.selected_template import fallback_selection
from ppt_agent.models.template_meta import (
    build_template_meta_from_ingest,
    default_template_meta,
)
from ppt_agent.retrieval.query_router import query
from ppt_agent.retrieval.template_index import (
    get_slide_image_paths,
    get_slide_zones,
    load_template_index,
    load_template_meta,
)

logger = logging.getLogger(__name__)


def run(workspace: JobWorkspace, force: bool = False) -> list[Path]:
    selected_path = workspace.artifact_path("selected_template")
    meta_path = workspace.artifact_path("template_meta")
    zones_path = workspace.artifact_path("template_zones")
    if selected_path.exists() and meta_path.exists() and not force:
        return [selected_path, meta_path]

    outline = load_artifact(workspace, "outline")
    slide_count = outline["meta"]["total_slides"]
    templates = load_template_index()

    # Build search query from outline metadata
    query_parts = [
        outline["meta"].get("domain", ""),
        outline["meta"].get("audience", ""),
        outline["meta"].get("tone", ""),
    ]
    query_text = " ".join(p for p in query_parts if p)

    # Search templates via RAG pipeline
    ranked = query(
        [
            {
                "id": item["template_id"],
                "template_id": item["template_id"],
                "text": item.get("retrieval_text", ""),
                **item,
            }
            for item in templates
        ],
        query_text,
        ["bm25"],
        3,
    )

    # Determine if we have a real match or need fallback
    best_match = None
    if ranked and ranked[0].get("score", 0) > 0:
        best_id = ranked[0]["template_id"]
        # Find the full entry from the index
        for entry in templates:
            if entry["template_id"] == best_id:
                best_match = entry
                break

    if best_match and best_match["template_id"] != "fallback.default":
        # Real template matched — load its full metadata
        selected = {
            "template_id": best_match["template_id"],
            "selection_status": "matched",
            "score": min(1.0, float(ranked[0].get("score", 0))),
            "reason": f"Matched template '{best_match['template_id']}' via BM25 retrieval.",
            "template_path": best_match.get("path", ""),
            "color_scheme": best_match.get("color_scheme", ""),
            "ranking": [
                {
                    "template_id": item["template_id"],
                    "score": min(1.0, float(item.get("score", 0))),
                    "rank": rank,
                    "domain_fit": 0.8 if item["template_id"] == best_match["template_id"] else 0.5,
                    "layout_fit": 0.7,
                    "tone_fit": 0.7,
                    "notes": "Ranked by BM25 + TF-IDF retrieval.",
                }
                for rank, item in enumerate(ranked, start=1)
            ],
            "warnings": [],
        }

        # Load real template meta
        try:
            ingest_meta = load_template_meta(best_match)
            meta = build_template_meta_from_ingest(ingest_meta)
        except FileNotFoundError:
            logger.warning("Template meta not found for '%s', using default", best_match["template_id"])
            meta = default_template_meta(slide_count)

        # Build template zones mapping (slide images + zones for downstream)
        template_zones = _build_template_zones(best_match, meta)

    else:
        # Fallback
        selected = fallback_selection()
        if ranked and ranked[0].get("score", 0) > 0:
            selected["ranking"] = [
                {
                    "template_id": item["template_id"],
                    "score": min(1.0, float(item.get("score", 0))),
                    "rank": rank,
                    "domain_fit": 0.6,
                    "layout_fit": 0.7,
                    "tone_fit": 0.6,
                    "notes": "Ranked by local BM25 retrieval.",
                }
                for rank, item in enumerate(ranked, start=1)
            ]
        meta = default_template_meta(slide_count)
        template_zones = _build_fallback_zones(slide_count)

    outputs = [
        write_artifact(workspace, "selected_template", selected),
        write_artifact(workspace, "template_meta", meta),
        write_artifact(workspace, "template_zones", template_zones),
    ]
    return outputs


def _build_template_zones(entry: dict, meta: dict) -> dict:
    """Build the template_zones artifact for a real template.

    Contains per-slide: image path (for img2img reference), text zones
    (for content overlay), and image zones (for visual areas).
    """
    slide_images = get_slide_image_paths(entry)
    slide_zones = get_slide_zones(entry)

    slides: list[dict] = []
    for slide in meta.get("slides", []):
        idx = slide["index"]
        img_path = slide_images.get(idx)

        # Separate text zones from decoration/image zones
        text_zones = [z for z in slide.get("zones", []) if z.get("type") in ("title", "subtitle", "body", "footer")]
        image_zones = [z for z in slide.get("zones", []) if z.get("type") == "image"]

        slides.append({
            "index": idx,
            "template_image": str(img_path) if img_path else None,
            "text_zones": text_zones,
            "image_zones": image_zones,
            "all_zones": slide.get("zones", []),
            "layout": slide.get("layout", "fallback.basic"),
        })

    return {
        "template_id": meta.get("template_id", "unknown"),
        "color_scheme": meta.get("color_scheme", ""),
        "theme_colors": meta.get("theme_colors", {}),
        "slide_count": len(slides),
        "slides": slides,
    }


def _build_fallback_zones(slide_count: int) -> dict:
    """Build template_zones for the fallback template (no real images)."""
    from ppt_agent.models.template_meta import DEFAULT_ZONES

    slides = []
    for i in range(slide_count):
        text_zones = [z for z in DEFAULT_ZONES if z["type"] in ("title", "body")]
        image_zones = [z for z in DEFAULT_ZONES if z["type"] == "image"]
        slides.append({
            "index": i,
            "template_image": None,
            "text_zones": text_zones,
            "image_zones": image_zones,
            "all_zones": DEFAULT_ZONES,
            "layout": "cover.hero" if i == 0 else "fallback.basic",
        })

    return {
        "template_id": "fallback.default",
        "color_scheme": "blue",
        "theme_colors": {
            "primary": "#1F4E79",
            "secondary": "#70AD47",
            "accent": "#F4B183",
            "background": "#FFFFFF",
        },
        "slide_count": slide_count,
        "slides": slides,
    }
