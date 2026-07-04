"""Template index loader.

Reads ``templates/index.json`` and provides helpers to resolve
template assets (slide images, meta.json, template.pptx).

The index entry schema (new, with slide image support)::

    {
        "template_id": "blue-medical.report",
        "path": "blue-medical/report",          # relative to templates/
        "color_scheme": "blue",
        "domain_tags": ["medical"],
        "tone_tags": ["professional"],
        "slide_count": 10,
        "retrieval_text": "blue medical professional ..."
    }
"""

from __future__ import annotations

import json
from pathlib import Path

# Default templates root — can be overridden
_DEFAULT_INDEX = Path("templates/index.json")


def load_template_index(path: Path = _DEFAULT_INDEX) -> list[dict]:
    """Load the template index as a list of entry dicts."""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return data.get("templates", [])
    return data


def resolve_template_dir(
    entry: dict,
    templates_root: Path = Path("templates"),
) -> Path:
    """Resolve the filesystem path to a template's directory."""
    return templates_root / entry["path"]


def load_template_meta(
    entry: dict,
    templates_root: Path = Path("templates"),
) -> dict:
    """Load the full meta.json for a template entry."""
    tpl_dir = resolve_template_dir(entry, templates_root)
    meta_path = tpl_dir / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"Template meta not found: {meta_path}")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def get_slide_image_paths(
    entry: dict,
    templates_root: Path = Path("templates"),
) -> dict[int, Path]:
    """Return a mapping of slide_index → image path for a template.

    Reads meta.json to get per-slide image paths.  Falls back to
    scanning the slides/ directory for slide_00.png, slide_01.png, ...
    """
    tpl_dir = resolve_template_dir(entry, templates_root)

    # Try meta.json first
    meta_path = tpl_dir / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        result: dict[int, Path] = {}
        for slide in meta.get("slides", []):
            img_rel = slide.get("image_path")
            if img_rel:
                img_path = tpl_dir / img_rel
                if img_path.exists():
                    result[slide["index"]] = img_path
        if result:
            return result

    # Fallback: scan slides/ directory
    slides_dir = tpl_dir / "slides"
    if not slides_dir.exists():
        return {}

    result = {}
    for png in sorted(slides_dir.glob("slide_*.png")):
        # Extract index from filename like slide_00.png
        stem = png.stem  # "slide_00"
        try:
            idx = int(stem.split("_")[1])
            result[idx] = png
        except (IndexError, ValueError):
            continue
    return result


def get_slide_zones(
    entry: dict,
    templates_root: Path = Path("templates"),
) -> dict[int, list[dict]]:
    """Return a mapping of slide_index → zone list for a template.

    Each zone has: zone_id, type, position [x,y,w,h], text, confidence.
    """
    tpl_dir = resolve_template_dir(entry, templates_root)
    meta_path = tpl_dir / "meta.json"
    if not meta_path.exists():
        return {}

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    result: dict[int, list[dict]] = {}
    for slide in meta.get("slides", []):
        result[slide["index"]] = slide.get("zones", [])
    return result
