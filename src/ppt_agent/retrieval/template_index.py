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


# ═══════════════════════════════════════════════════════════════════════
#  3-Layer Chunking for RAG Retrieval
# ═══════════════════════════════════════════════════════════════════════


def build_template_chunks(entry: dict, templates_root: Path = TEMPLATES_ROOT) -> list[dict]:
    """Build 3 retrieval chunks per template from its meta.json.

    Returns a list of 3 chunk documents, each with:
        - chunk_id: unique identifier (template_id + chunk type)
        - template_id: parent template
        - chunk_type: "overview" | "design" | "slides"
        - text: rich searchable text
        - weight: aggregation weight

    This replaces the single ``retrieval_text`` with structured,
    multi-faceted chunks that capture different dimensions of the template.
    """
    tpl_dir = resolve_template_dir(entry, templates_root)
    meta_path = tpl_dir / "meta.json"
    if not meta_path.exists():
        return []

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    template_id = entry["template_id"]
    domain = meta.get("domain", entry.get("domain_tags", ["general"])[0] if entry.get("domain_tags") else "general")
    style = meta.get("style", entry.get("tone_tags", ["professional"])[0] if entry.get("tone_tags") else "professional")
    scene = meta.get("scene", [])
    slide_count = meta.get("slide_count", 0)
    slides = meta.get("slides", [])
    color_scheme = meta.get("color_scheme", {})
    design_audit = meta.get("design_audit", {})

    chunks = []

    # ── Chunk 1: Overview (权重 0.4) ──
    # Dense keyword-rich text for BM25 + vector matching
    scene_list = ", ".join(scene) if scene else "通用"
    scene_en = _map_scene_to_en(scene_list)

    # Keyword taxonomies for better cross-lingual matching
    domain_kw = _domain_keywords_map.get(domain, [])
    style_kw = _style_keywords_map.get(style, [])

    # ── Collect font names first (used by both overview and design) ──
    fonts = set()
    title_n = 0
    body_n = 0
    image_n = 0
    all_slide_text = []
    for s in slides:
        for z in s.get("zones", []):
            txt = (z.get("text") or "").strip()
            if txt and len(txt) > 1:
                all_slide_text.append(txt)
            fmt = z.get("formatting", {})
            fn = fmt.get("font_name")
            if fn: fonts.add(fn)
            ztype = z.get("type", "")
            if ztype == "title": title_n += 1
            elif ztype in ("body", "bullets"): body_n += 1
            elif ztype == "image": image_n += 1

    # ── Collect template-unique content ──
    template_name_kw = template_id.replace(".", " ").replace("-", " ").replace("_", " ")
    unique_names = " ".join(template_name_kw.split() * 3)  # repeat for BM25 TF boost
    slide_text_blob = " ".join(all_slide_text[:30])

    # Unique numeric features
    unique_features = [f"{slide_count}slides"]
    if isinstance(color_scheme, dict):
        unique_features.append(" ".join(
            f"{k}{v}" for k, v in color_scheme.items()
            if k in ("primary", "secondary", "accent")
        ))
    elif isinstance(color_scheme, str):
        unique_features.append(color_scheme)
    if fonts:
        unique_features.append("fonts " + " ".join(sorted(fonts)[:3]))
    unique_features.append(f"titles{title_n} bodies{body_n} images{image_n}")

    overview_text = " ".join([
        unique_names,
        slide_text_blob,
        " ".join(unique_features),
        f"{domain} {style} {scene_list}",
    ])

    chunks.append({
        "chunk_id": f"{template_id}::overview",
        "template_id": template_id,
        "chunk_type": "overview",
        "text": overview_text,
        "weight": 0.4,
        "domain": domain,
        "style": style,
        "scene": scene_list,
    })

    # ── Chunk 2: Design (权重 0.2) ──
    design_parts = []
    if isinstance(color_scheme, dict):
        for key, hex_val in color_scheme.items():
            if key in ("primary", "secondary", "accent", "background"):
                color_name = _hex_to_color_name(hex_val)
                design_parts.append(f"{key}色{hex_val}({color_name})")
    elif isinstance(color_scheme, str):
        design_parts.append(f"{color_scheme}色系 {color_scheme} color scheme")

    if fonts:
        design_parts.append(f"字体fonts: {' '.join(sorted(fonts)[:4])}")
    design_parts.append(f"标题{title_n}正文{body_n}图片{image_n}")
    design_text = " ".join(design_parts)

    chunks.append({
        "chunk_id": f"{template_id}::design",
        "template_id": template_id,
        "chunk_type": "design",
        "text": design_text,
        "weight": 0.2,
    })

    # ── Chunk 3: Slides 结构摘要 (权重 0.4) ──
    layout_counts: dict[str, int] = {}
    layout_examples: dict[str, str] = {}

    for s in slides:
        layout = s.get("layout", "content")
        layout_counts[layout] = layout_counts.get(layout, 0) + 1

        if layout not in layout_examples:
            zones = s.get("zones", [])
            text_zone_types = [z.get("type", "?") for z in zones
                              if z.get("type") in ("title", "subtitle", "body", "bullets")]
            zone_summary = "+".join(text_zone_types[:4]) or "empty"
            layout_examples[layout] = f"{layout}布局({zone_summary})"

    slides_parts = [f"共{slide_count}页模板 {slide_count}-slide template"]
    for layout, count in sorted(layout_counts.items(), key=lambda x: -x[1]):
        example = layout_examples.get(layout, "")
        slides_parts.append(f"{count}页{example} {count}pages {layout} layout")

    # First and last page info
    layout_seq = [s.get("layout", "content") for s in slides]
    slides_parts.append(f"首页{layout_seq[0] if layout_seq else 'cover'}布局")
    if len(slides) > 2:
        slides_parts.append(f"尾页{layout_seq[-1] if layout_seq else 'content'}布局")

    # Slide sequence with actual text content for richer matching
    content_snippets = []
    for s in slides[:8]:
        zones = s.get("zones", [])
        for z in zones:
            txt = z.get("text", "").strip()
            if txt and len(txt) > 2 and z.get("type") in ("title", "body"):
                content_snippets.append(txt[:40])
                break
    if content_snippets:
        slides_parts.append(f"示例文字: {'; '.join(content_snippets[:5])}")

    slides_text = " ".join(slides_parts)

    chunks.append({
        "chunk_id": f"{template_id}::slides",
        "template_id": template_id,
        "chunk_type": "slides",
        "text": slides_text,
        "weight": 0.4,
    })

    return chunks


# ── Keyword mapping for bilingual chunk text ──

_domain_keywords_map: dict[str, list[str]] = {
    "tech": ["技术", "科技", "软件", "AI", "人工智能", "互联网", "数字", "tech", "software", "AI", "digital"],
    "medical": ["医疗", "医药", "健康", "临床", "生物", "medical", "healthcare", "clinical", "pharma"],
    "business": ["商业", "金融", "咨询", "企业", "投资", "business", "finance", "corporate", "consulting"],
    "education": ["教育", "学术", "培训", "课程", "研究", "education", "academic", "training", "research"],
    "general": ["通用", "多用途", "general", "multi-purpose", "万能", "all-purpose"],
}

_style_keywords_map: dict[str, list[str]] = {
    "modern": ["现代", "modern", "简洁", "clean", "科技感"],
    "professional": ["专业", "professional", "商务", "企业", "corporate"],
    "clean": ["干净", "clean", "简约", "minimal", "清爽"],
    "warm": ["温暖", "warm", "友好", "friendly", "亲和"],
    "bold": ["大胆", "bold", "突出", "醒目", "视觉冲击"],
}

# Scene → English mapping
_SCENE_EN: dict[str, str] = {
    "pitch": "pitch investor fundraising",
    "report": "report analysis findings summary",
    "product_intro": "product launch feature introduction",
    "competition": "competition award challenge contest",
    "lecture": "lecture lesson training education",
    "project_report": "project report timeline milestone status",
}


def _map_scene_to_en(scene_list: str) -> str:
    """Map Chinese/English scene names to English keywords."""
    parts = []
    for s in scene_list.split(", "):
        mapped = _SCENE_EN.get(s.strip(), s)
        parts.append(mapped)
    return " ".join(parts)


def _hex_to_color_name(hex_color: str) -> str:
    """Quick color name lookup."""
    hex_lower = hex_color.lstrip("#").lower()
    mapping = {
        "1f4e79": "darkblue", "2e75b6": "blue", "70ad47": "green",
        "f4b183": "peach", "ffffff": "white", "000000": "black",
        "333333": "darkgray", "ed7d31": "orange", "4472c4": "blue",
        "a5a5a5": "gray", "ffc000": "gold", "5b9bd5": "lightblue",
        "c00000": "red",
    }
    return mapping.get(hex_lower, hex_lower[:6])


def load_template_chunks() -> list[dict]:
    """Load all templates as 3-layer chunks for RAG retrieval.

    Returns a flat list of chunk documents (3 per template).
    """
    entries = load_template_index()
    all_chunks = []
    for entry in entries:
        chunks = build_template_chunks(entry)
        all_chunks.extend(chunks)
    logger.debug("Built %d chunks from %d templates", len(all_chunks), len(entries))
    return all_chunks
