"""Template index loader with auto-discovery.

Auto-discovers templates by scanning ``templates/*/*/meta.json``.
No manual ``index.json`` editing required — just drop a template folder
into the right category and it will be picked up automatically.

If ``templates/index.json`` exists, its entries are **merged** with
auto-discovered ones (manual entries take precedence for the same
``template_id``), allowing custom ``retrieval_text`` overrides.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Default templates root
TEMPLATES_ROOT = Path("templates")

# Directories to skip during auto-discovery
_SKIP_DIRS = {"sample", "__pycache__", ".git"}


def load_template_index(path: Path | None = None) -> list[dict]:
    """Load the template index, auto-discovering from meta.json files.

    1. Scans ``templates/<category>/<name>/meta.json``
    2. Auto-generates index entries (retrieval_text, domain_tags, etc.)
    3. Merges with ``index.json`` if present (manual overrides win)
    """
    root = path.parent if path else TEMPLATES_ROOT
    index_json = root / "index.json"

    # Auto-discover from filesystem
    discovered = _discover_templates(root)

    # Merge with manual index.json entries (manual wins)
    if index_json.exists():
        try:
            manual = json.loads(index_json.read_text(encoding="utf-8"))
            manual_entries = manual.get("templates", []) if isinstance(manual, dict) else manual
            discovered = _merge(manual_entries, discovered)
        except Exception:
            logger.warning("Failed to read index.json, using auto-discovered only", exc_info=True)

    return discovered


# ── Auto-discovery ────────────────────────────────────────────────────


def _discover_templates(root: Path) -> list[dict]:
    """Scan all meta.json files under templates/ and build index entries."""
    entries: list[dict] = []

    for meta_path in sorted(root.glob("*/*/meta.json")):
        category = meta_path.parent.parent.name
        if category in _SKIP_DIRS:
            continue

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Skipping invalid meta.json: %s", meta_path)
            continue

        template_id = meta.get("template_id")
        if not template_id:
            logger.warning("meta.json missing template_id: %s", meta_path)
            continue

        entry = _build_entry(meta, category, meta_path.parent)
        entries.append(entry)

    return entries


def _build_entry(meta: dict, category: str, tpl_dir: Path) -> dict:
    """Build an index entry from a template's meta.json."""
    template_id = meta["template_id"]
    domain = meta.get("domain", category)
    style = meta.get("style", "general")
    scene = meta.get("scene", [])
    slide_count = meta.get("slide_count", 0)
    color_scheme = meta.get("color_scheme", {})
    color_name = color_scheme if isinstance(color_scheme, str) else (
        _guess_color_name(color_scheme) if isinstance(color_scheme, dict) else "neutral"
    )

    # Generate tags
    domain_tags = list(set([domain] + scene))
    tone_tags = [style]

    # Auto-generate retrieval_text from all fields
    retrieval_parts = [style, color_name, domain] + scene + [template_id]
    retrieval_text = " ".join(retrieval_parts).lower()

    return {
        "template_id": template_id,
        "path": str(tpl_dir.relative_to(TEMPLATES_ROOT)),
        "color_scheme": color_name,
        "domain_tags": domain_tags,
        "tone_tags": tone_tags,
        "slide_count": slide_count,
        "retrieval_text": retrieval_text,
        # Auto-discovered marker (not from manual index.json)
        "_auto": True,
    }


def _guess_color_name(color_scheme: dict) -> str:
    """Guess a color name from a color_scheme dict by parsing the primary hex."""
    primary = color_scheme.get("primary", "").lstrip("#")
    if not primary or len(primary) < 6:
        return "neutral"

    try:
        r, g, b = int(primary[0:2], 16), int(primary[2:4], 16), int(primary[4:6], 16)
    except ValueError:
        return "neutral"

    # HSL-like heuristic
    max_c, min_c = max(r, g, b), min(r, g, b)
    delta = max_c - min_c
    lightness = (max_c + min_c) / 2 / 255

    # Achromatic (low saturation)
    if delta < 35:
        if lightness > 0.85:
            return "white"
        elif lightness < 0.25:
            return "dark"
        else:
            return "gray" if lightness < 0.6 else "neutral"

    # Chromatic — pick by dominant channel
    if max_c == r:
        return "orange" if g > 120 else "red" if g < 90 else "pink"
    elif max_c == g:
        return "green"
    else:
        return "blue"


# ── Merge: manual entries override auto-discovered ────────────────────


def _merge(manual: list[dict], auto: list[dict]) -> list[dict]:
    """Merge manual index.json entries with auto-discovered ones.

    Manual entries for the same template_id take precedence.
    Manual entries with a path not found in auto-discovery are appended.
    """
    auto_by_id = {e["template_id"]: e for e in auto}
    result: list[dict] = []
    seen: set[str] = set()

    for entry in manual:
        tid = entry.get("template_id", "")
        if tid in auto_by_id:
            # Manual override: use manual values, fill gaps from auto
            merged = {**auto_by_id[tid], **entry, "_auto": False}
            result.append(merged)
        else:
            result.append({**entry, "_auto": False})
        seen.add(tid)

    # Append auto-discovered entries not in manual
    for entry in auto:
        if entry["template_id"] not in seen:
            result.append(entry)

    return result


# ── Asset helpers ─────────────────────────────────────────────────────


def resolve_template_dir(
    entry: dict,
    templates_root: Path = TEMPLATES_ROOT,
) -> Path:
    """Resolve the filesystem path to a template's directory."""
    return templates_root / entry["path"]


def load_template_meta(
    entry: dict,
    templates_root: Path = TEMPLATES_ROOT,
) -> dict:
    """Load the full meta.json for a template entry."""
    tpl_dir = resolve_template_dir(entry, templates_root)
    meta_path = tpl_dir / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"Template meta not found: {meta_path}")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def get_slide_image_paths(
    entry: dict,
    templates_root: Path = TEMPLATES_ROOT,
) -> dict[int, Path]:
    """Return a mapping of slide_index → image path for a template.

    Reads meta.json to get per-slide image paths.  Falls back to
    scanning the preview/ directory for slide_00.png, slide_01.png, ...
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

    # Fallback: scan preview/ directory
    preview_dir = tpl_dir / "preview"
    if not preview_dir.exists():
        return {}

    result = {}
    for png in sorted(preview_dir.glob("slide_*.png")):
        stem = png.stem
        try:
            idx = int(stem.split("_")[1])
            result[idx] = png
        except (IndexError, ValueError):
            continue
    return result


def get_slide_zones(
    entry: dict,
    templates_root: Path = TEMPLATES_ROOT,
) -> dict[int, list[dict]]:
    """Return a mapping of slide_index → zone list for a template."""
    tpl_dir = resolve_template_dir(entry, templates_root)
    meta_path = tpl_dir / "meta.json"
    if not meta_path.exists():
        return {}

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    result: dict[int, list[dict]] = {}
    for slide in meta.get("slides", []):
        result[slide["index"]] = slide.get("zones", [])
    return result
