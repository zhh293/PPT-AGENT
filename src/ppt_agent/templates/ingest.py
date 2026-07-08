"""Template ingestion — PPTX → slide images + OCR → index entry.

Two rendering paths for PPTX → images:

1. **High-fidelity** (LibreOffice available):
   pptx → soffice --convert-to pdf → pdf2image → per-slide PNG

2. **Fallback** (no LibreOffice):
   User provides pre-exported slide images, OR we generate placeholder
   structure from python-pptx shape analysis alone (no visual rendering).

Usage::

    from ppt_agent.templates.ingest import ingest_template

    entry = ingest_template(
        pptx_path="path/to/template.pptx",
        color_scheme="blue",
        domain="medical",
    )
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.util import Emu

from ppt_agent.tools.ocr import classify_zones, ocr_slide_image

logger = logging.getLogger(__name__)

# Default template library root (relative to project)
TEMPLATES_ROOT = Path("templates")


def ingest_template(
    pptx_path: str | Path,
    color_scheme: str,
    domain: str,
    *,
    tone_tags: list[str] | None = None,
    templates_root: Path | None = None,
    force: bool = False,
) -> dict:
    """Ingest a single PPTX template into the template library.

    Steps:
        1. Create template directory: templates/<color>-<domain>/<stem>/
        2. Copy the original .pptx file
        3. Export each slide as a PNG image
        4. Run OCR on each slide image → zone structure
        5. Extract color info from the PPTX theme
        6. Write meta.json
        7. Update templates/index.json

    Parameters
    ----------
    pptx_path
        Path to the source .pptx file.
    color_scheme
        Color category, e.g. "blue", "green", "orange".
    domain
        Industry/domain, e.g. "medical", "education", "tech".
    tone_tags
        Optional tone tags, e.g. ["professional", "modern"].
    templates_root
        Override the templates directory (default: ./templates).
    force
        If True, overwrite existing template directory.

    Returns the template index entry dict.
    """
    pptx_path = Path(pptx_path).resolve()
    if not pptx_path.exists():
        raise FileNotFoundError(f"PPTX not found: {pptx_path}")

    root = (templates_root or TEMPLATES_ROOT).resolve()
    category_dir = root / f"{color_scheme}-{domain}"
    template_id = f"{color_scheme}-{domain}.{pptx_path.stem}"
    template_dir = category_dir / pptx_path.stem

    if template_dir.exists():
        if not force:
            raise FileExistsError(
                f"Template directory already exists: {template_dir}. "
                "Use force=True to overwrite."
            )
        shutil.rmtree(template_dir)

    template_dir.mkdir(parents=True, exist_ok=True)
    slides_dir = template_dir / "slides"
    slides_dir.mkdir(exist_ok=True)

    # 1. Copy original PPTX
    dest_pptx = template_dir / "template.pptx"
    shutil.copy2(pptx_path, dest_pptx)

    # 2. Open with python-pptx for metadata
    prs = Presentation(str(pptx_path))
    slide_count = len(prs.slides)
    theme_colors = _extract_theme_colors(prs)

    # 3. Export slides to images
    slide_images = _export_slides_to_images(pptx_path, slides_dir, slide_count)

    # 4. OCR each slide image → zone descriptions
    slides_meta: list[dict] = []
    all_ocr_text: list[str] = []

    for idx in range(slide_count):
        image_path = slide_images.get(idx)
        zones: list[dict] = []
        ocr_text = ""

        if image_path and image_path.exists():
            regions = ocr_slide_image(image_path)
            zones = classify_zones(regions, slide_index=idx)
            ocr_text = " ".join(r.text for r in regions)
            all_ocr_text.append(ocr_text)
        else:
            # Fallback: extract zone info from python-pptx shapes
            zones = _zones_from_pptx_shapes(prs.slides[idx], idx)

        # Also extract shape-level info for richer zone data
        pptx_zones = _zones_from_pptx_shapes(prs.slides[idx], idx)

        # Merge: prefer OCR zones for text, supplement with shape zones
        merged_zones = _merge_zone_sources(zones, pptx_zones)

        slide_meta = {
            "index": idx,
            "image_path": str(image_path.relative_to(template_dir)) if image_path and image_path.exists() else None,
            "zones": merged_zones,
            "ocr_text": ocr_text,
        }
        slides_meta.append(slide_meta)

    # 5. Build retrieval text
    retrieval_parts = [color_scheme, domain]
    if tone_tags:
        retrieval_parts.extend(tone_tags)
    retrieval_parts.extend(all_ocr_text[:3])  # First 3 slides' OCR text
    retrieval_text = " ".join(retrieval_parts)
    # Truncate for index efficiency
    if len(retrieval_text) > 500:
        retrieval_text = retrieval_text[:500]

    # 6. Write meta.json
    meta = {
        "template_id": template_id,
        "style": f"{color_scheme} {domain} template",
        "slide_count": slide_count,
        "color_scheme": color_scheme,
        "domain": domain,
        "domain_tags": [domain],
        "tone_tags": tone_tags or ["professional"],
        "theme_colors": theme_colors,
        "retrieval_text": retrieval_text,
        "slides": slides_meta,
    }

    meta_path = template_dir / "meta.json"
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # 7. Update index.json
    index_entry = {
        "template_id": template_id,
        "path": str(template_dir.relative_to(root)),
        "color_scheme": color_scheme,
        "domain_tags": [domain],
        "tone_tags": tone_tags or ["professional"],
        "slide_count": slide_count,
        "retrieval_text": retrieval_text,
    }
    _update_index(root, index_entry)

    logger.info(
        "Ingested template '%s': %d slides, %d zones total",
        template_id, slide_count,
        sum(len(s["zones"]) for s in slides_meta),
    )

    return index_entry


def _export_slides_to_images(
    pptx_path: Path,
    output_dir: Path,
    slide_count: int,
) -> dict[int, Path]:
    """Export PPTX slides to PNG images.

    Tries LibreOffice (high fidelity) first, falls back to user-provided
    images or empty dict.
    """
    soffice = shutil.which("libreoffice") or shutil.which("soffice")

    if soffice:
        return _export_via_libreoffice(pptx_path, output_dir, soffice)

    # No LibreOffice — check if user pre-exported images
    existing = _find_existing_slide_images(output_dir, slide_count)
    if existing:
        logger.info("Using %d pre-exported slide images", len(existing))
        return existing

    logger.warning(
        "LibreOffice not available and no pre-exported images found. "
        "Slide images will be missing — OCR zone detection will be skipped. "
        "Install LibreOffice or place slide_00.png, slide_01.png, ... in %s",
        output_dir,
    )
    return {}


def _export_via_libreoffice(
    pptx_path: Path,
    output_dir: Path,
    soffice_bin: str,
) -> dict[int, Path]:
    """pptx → pdf → per-slide PNG via LibreOffice + pdf2image."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # Convert PPTX to PDF
        try:
            subprocess.run(
                [soffice_bin, "--headless", "--convert-to", "pdf",
                 "--outdir", str(tmp_path), str(pptx_path)],
                capture_output=True, text=True, timeout=120,
                check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning("LibreOffice conversion failed: %s", e)
            return {}

        pdf_files = list(tmp_path.glob("*.pdf"))
        if not pdf_files:
            logger.warning("LibreOffice produced no PDF output")
            return {}

        # PDF → images via pdf2image
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(str(pdf_files[0]), dpi=150)
        except Exception as e:
            logger.warning("pdf2image conversion failed: %s", e)
            return {}

        result: dict[int, Path] = {}
        for idx, img in enumerate(images):
            out_path = output_dir / f"slide_{idx:02d}.png"
            img.save(str(out_path), "PNG")
            result[idx] = out_path

        return result


def _find_existing_slide_images(
    output_dir: Path,
    slide_count: int,
) -> dict[int, Path]:
    """Check for pre-exported slide images (slide_00.png, slide_01.png, ...)."""
    result: dict[int, Path] = {}
    for idx in range(slide_count):
        for pattern in [f"slide_{idx:02d}.png", f"slide_{idx}.png", f"slide{idx}.png"]:
            p = output_dir / pattern
            if p.exists():
                result[idx] = p
                break
    # Also check for any numbered PNG files
    if not result:
        pngs = sorted(output_dir.glob("*.png"))
        for idx, png in enumerate(pngs):
            if idx < slide_count:
                result[idx] = png
    return result


def _extract_theme_colors(prs: Presentation) -> dict:
    """Extract theme colors from the PPTX slide master.

    Returns a dict with primary, secondary, accent, background as hex strings.
    Falls back to sensible defaults if extraction fails.
    """
    defaults = {
        "primary": "#1F4E79",
        "secondary": "#70AD47",
        "accent": "#F4B183",
        "background": "#FFFFFF",
    }

    try:
        # Access the theme via slide master XML
        master = prs.slide_masters[0]
        theme_xml = master.element
        # Look for clrScheme in the theme
        ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}

        # Try to find color scheme elements
        for clr_scheme in theme_xml.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}clrScheme"):
            colors = {}
            for child in clr_scheme:
                tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                # Get the color value from srgbClr or sysClr
                for color_elem in child:
                    color_tag = color_elem.tag.split("}")[-1] if "}" in color_elem.tag else color_elem.tag
                    if color_tag == "srgbClr":
                        colors[tag] = f"#{color_elem.get('val', '000000')}"
                    elif color_tag == "sysClr":
                        colors[tag] = f"#{color_elem.get('lastClr', '000000')}"

            if colors:
                return {
                    "primary": colors.get("dk1", colors.get("accent1", defaults["primary"])),
                    "secondary": colors.get("accent2", defaults["secondary"]),
                    "accent": colors.get("accent1", defaults["accent"]),
                    "background": colors.get("lt1", defaults["background"]),
                    "raw": colors,  # Keep all extracted colors
                }
    except Exception as e:
        logger.debug("Theme color extraction failed: %s", e)

    return defaults


def _zones_from_pptx_shapes(slide, slide_index: int,
                             slide_w: int | None = None,
                             slide_h: int | None = None) -> list[dict]:
    """Extract zone information from python-pptx shape objects.

    This is the fallback when no slide image is available for OCR.
    Uses shape positions, types, and placeholder info.
    """
    if slide_w is None:
        try:
            slide_w = slide.part.slide_layout.part.package.presentation_part.presentation.slide_width
        except AttributeError:
            slide_w = 12192000
    if slide_h is None:
        try:
            slide_h = slide.part.slide_layout.part.package.presentation_part.presentation.slide_height
        except AttributeError:
            slide_h = 6858000

    zones: list[dict] = []
    for i, shape in enumerate(slide.shapes):
        # Normalise position to 0-1 fractions
        x = shape.left / slide_w if shape.left else 0.0
        y = shape.top / slide_h if shape.top else 0.0
        w = shape.width / slide_w if shape.width else 0.0
        h = shape.height / slide_h if shape.height else 0.0

        # Determine zone type
        zone_type = "decoration"
        text_content = ""

        if shape.has_text_frame:
            texts = [p.text for p in shape.text_frame.paragraphs if p.text.strip()]
            text_content = "\n".join(texts)
            try:
                ph_format = shape.placeholder_format
            except ValueError:
                ph_format = None
            if ph_format is not None:
                ph_type = ph_format.type
                # PlaceholderType: TITLE=1, CENTER_TITLE=3, SUBTITLE=4, BODY=13
                if ph_type in (1, 3, 15):
                    zone_type = "title"
                elif ph_type in (2, 4):
                    zone_type = "subtitle"
                elif ph_type in (13, 14):
                    zone_type = "body"
                else:
                    zone_type = "body"
            else:
                # Non-placeholder text box
                if y < 0.25 and h > 0.03:
                    zone_type = "title"
                elif y > 0.85:
                    zone_type = "footer"
                else:
                    zone_type = "body"

        elif hasattr(shape, "image"):
            try:
                _ = shape.image
                zone_type = "image"
            except Exception:
                zone_type = "decoration"

        zones.append({
            "zone_id": f"s{slide_index}_shape{i}",
            "type": zone_type,
            "position": [round(x, 4), round(y, 4), round(w, 4), round(h, 4)],
            "text": text_content,
            "confidence": 100.0,  # Shape-based extraction is exact
            "source": "pptx_shape",
        })

    return zones


def _merge_zone_sources(
    ocr_zones: list[dict],
    shape_zones: list[dict],
) -> list[dict]:
    """Merge OCR-detected zones with python-pptx shape zones.

    OCR zones have visual text positions; shape zones have structural info.
    We keep all shape zones and supplement with OCR zones that don't overlap.
    """
    if not ocr_zones:
        return shape_zones
    if not shape_zones:
        return ocr_zones

    merged = list(shape_zones)  # Start with shape zones (structural truth)

    # Add OCR zones that don't significantly overlap with any shape zone
    for ocr_z in ocr_zones:
        overlaps = False
        for shape_z in shape_zones:
            if _zones_overlap(ocr_z["position"], shape_z["position"], threshold=0.3):
                # Supplement the shape zone with OCR text if it has no text
                if not shape_z.get("text") and ocr_z.get("text"):
                    shape_z["ocr_text"] = ocr_z["text"]
                overlaps = True
                break
        if not overlaps:
            ocr_z["source"] = "ocr"
            merged.append(ocr_z)

    return merged


def _zones_overlap(
    pos_a: list[float],
    pos_b: list[float],
    threshold: float = 0.3,
) -> bool:
    """Check if two zones overlap by more than threshold (IoU-like)."""
    ax, ay, aw, ah = pos_a
    bx, by, bw, bh = pos_b

    # Intersection
    ix = max(ax, bx)
    iy = max(ay, by)
    ix2 = min(ax + aw, bx + bw)
    iy2 = min(ay + ah, by + bh)

    if ix2 <= ix or iy2 <= iy:
        return False

    intersection = (ix2 - ix) * (iy2 - iy)
    area_a = aw * ah
    area_b = bw * bh

    if area_a == 0 and area_b == 0:
        return False

    # Overlap relative to smaller area
    min_area = min(area_a, area_b) if min(area_a, area_b) > 0 else max(area_a, area_b)
    if min_area == 0:
        return False

    return (intersection / min_area) > threshold


def _update_index(templates_root: Path, entry: dict) -> None:
    """Add or update an entry in templates/index.json."""
    index_path = templates_root / "index.json"

    if index_path.exists():
        data = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        data = {"templates": []}

    templates = data.get("templates", [])

    # Replace existing entry with same template_id
    templates = [t for t in templates if t.get("template_id") != entry["template_id"]]
    templates.append(entry)

    data["templates"] = templates
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# ═══════════════════════════════════════════════════════════════════════
#  One-click auto-ingest (no manual params needed)
# ═══════════════════════════════════════════════════════════════════════

# Keyword → domain mapping (matched against slide text content)
_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "medical": ["medical", "healthcare", "clinical", "patient", "doctor", "hospital",
                 "pharma", "biotech", "diagnosis", "treatment", "surgery", "disease"],
    "tech": ["software", "hardware", "algorithm", "AI", "machine learning",
              "cloud", "data", "code", "API", "startup", "SaaS", "platform",
              "technology", "digital", "app", "cyber", "blockchain",
              "芯片", "半导体", "集成电路", "工科", "工程", "电路", "处理器"],
    "business": ["revenue", "profit", "market", "finance", "investment", "ROI",
                  "strategy", "growth", "KPI", "stakeholder", "quarterly", "board",
                  "corporate", "consulting", "sales", "marketing", "client"],
    "education": ["student", "teacher", "course", "curriculum", "lecture",
                   "academic", "research", "learning", "training", "university",
                   "school", "education", "classroom", "seminar", "workshop"],
    "general": [],
}

# Style keywords → derived from content density, color, layout mix
_STYLE_KEYWORDS: dict[str, list[str]] = {
    "modern": ["modern", "digital", "innovation", "future", "next-gen", "AI-powered"],
    "professional": ["professional", "corporate", "enterprise", "solution", "service"],
    "clean": ["clean", "simple", "minimal", "clear", "streamlined"],
    "warm": ["warm", "friendly", "community", "care", "support", "together"],
    "bold": ["bold", "powerful", "leading", "dominant", "breakthrough"],
}

# Scene keywords
_SCENE_KEYWORDS: dict[str, list[str]] = {
    "pitch": ["pitch", "investor", "funding", "demo", "showcase"],
    "report": ["report", "analysis", "findings", "summary", "quarterly", "annual"],
    "product_intro": ["product", "feature", "launch", "release", "introducing"],
    "competition": ["competition", "award", "winner", "challenge", "contest"],
    "lecture": ["lecture", "lesson", "topic", "chapter", "module"],
    "project_report": ["project", "timeline", "milestone", "deliverable", "status"],
}


def auto_ingest(
    pptx_path: str | Path,
    templates_root: Path | None = None,
    *,
    force: bool = False,
) -> dict:
    """One-click template ingestion — drop a PPTX, everything else is auto-detected.

    Usage::

        from ppt_agent.templates.ingest import auto_ingest
        entry = auto_ingest("my-template.pptx")

    Auto-detects:
        - ``domain`` — from slide text keyword matching
        - ``style`` — from content/style keyword matching
        - ``scene`` — from content/scene keyword matching
        - ``color_scheme`` — from PPTX theme colors
        - ``template_id`` — from filename stem
        - ``slide zones`` — from PPTX shape positions
        - ``slide images`` — via LibreOffice if available

    Parameters
    ----------
    pptx_path
        Path to the source .pptx file.
    templates_root
        Override templates directory (default: ``./templates``).
    force
        If True, overwrite existing template directory.
    """
    pptx_path = Path(pptx_path).resolve()
    if not pptx_path.exists():
        raise FileNotFoundError(f"PPTX not found: {pptx_path}")

    root = (templates_root or TEMPLATES_ROOT).resolve()
    prs = Presentation(str(pptx_path))
    slide_count = len(prs.slides)

    # ── Auto-detect metadata ───────────────────────────────────────
    all_text = _collect_all_text(prs)
    theme_colors = _extract_theme_colors(prs)
    color_scheme = _detect_color_name(theme_colors)
    domain = _detect_domain(all_text, filename=pptx_path.stem)
    style = _detect_style(all_text)
    scene = _detect_scenes(all_text)

    # ── Build template_id ──────────────────────────────────────────
    name_stem = pptx_path.stem
    template_id = f"{domain}.{name_stem}"

    # ── Determine category directory ───────────────────────────────
    category_dir = root / domain
    template_dir = category_dir / name_stem

    if template_dir.exists():
        if not force:
            raise FileExistsError(
                f"Template directory already exists: {template_dir}. Use force=True."
            )
        shutil.rmtree(template_dir)

    template_dir.mkdir(parents=True, exist_ok=True)
    preview_dir = template_dir / "preview"
    preview_dir.mkdir(exist_ok=True)

    # ── Copy original PPTX ─────────────────────────────────────────
    shutil.copy2(pptx_path, template_dir / "template.pptx")

    # ── Export slide images ────────────────────────────────────────
    slide_images = _export_slides_to_images(pptx_path, preview_dir, slide_count)

    # ── Build per-slide zones ──────────────────────────────────────
    slides_meta: list[dict] = []
    all_ocr_text: list[str] = []

    for idx in range(slide_count):
        shape_zones = _zones_from_pptx_shapes(prs.slides[idx], idx)
        image_path = slide_images.get(idx)

        ocr_text = ""
        if image_path and image_path.exists():
            try:
                regions = ocr_slide_image(image_path)
                ocr_zones = classify_zones(regions, slide_index=idx)
                ocr_text = " ".join(r.text for r in regions)
                all_ocr_text.append(ocr_text)
                merged_zones = _merge_zone_sources(ocr_zones, shape_zones)
            except Exception:
                merged_zones = shape_zones
        else:
            merged_zones = shape_zones

        # Infer layout from zone pattern
        layout = _infer_layout(merged_zones, idx)

        slides_meta.append({
            "index": idx,
            "layout": layout,
            "zones": merged_zones,
        })

    # ── Write meta.json ────────────────────────────────────────────
    meta = {
        "template_id": template_id,
        "domain": domain,
        "scene": scene,
        "style": style,
        "slide_count": slide_count,
        "color_scheme": theme_colors,
        "slides": slides_meta,
    }

    meta_path = template_dir / "meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    logger.info("Auto-ingested '%s': domain=%s style=%s scene=%s slides=%d",
                template_id, domain, style, scene, slide_count)

    return {
        "template_id": template_id,
        "path": str(template_dir.relative_to(root)),
        "domain": domain,
        "style": style,
        "scene": scene,
        "slide_count": slide_count,
        "color_scheme": _detect_color_name(theme_colors),
    }


# ── Auto-detection helpers ────────────────────────────────────────────


def _collect_all_text(prs: Presentation) -> str:
    """Extract all text content from all slides."""
    parts: list[str] = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                for p in shape.text_frame.paragraphs:
                    t = p.text.strip()
                    if t:
                        parts.append(t)
    return " ".join(parts)


def _detect_domain(all_text: str, filename: str = "") -> str:
    """Auto-detect domain from slide text keywords AND filename."""
    text_lower = (all_text + " " + filename).lower()
    scores: dict[str, int] = {}
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.lower() in text_lower)
        if score > 0:
            scores[domain] = score
    if scores:
        return max(scores, key=scores.get)
    return "general"


def _detect_style(all_text: str) -> str:
    """Auto-detect style from content/style keywords."""
    text_lower = all_text.lower()
    for style, keywords in _STYLE_KEYWORDS.items():
        if any(kw.lower() in text_lower for kw in keywords):
            return style
    return "professional"


def _detect_scenes(all_text: str) -> list[str]:
    """Auto-detect applicable scenes from content keywords."""
    text_lower = all_text.lower()
    found: list[str] = []
    for scene, keywords in _SCENE_KEYWORDS.items():
        if any(kw.lower() in text_lower for kw in keywords):
            found.append(scene)
    return found if found else ["report"]


def _detect_color_name(theme_colors: dict) -> str:
    """Detect a human-readable color name from theme colors."""
    primary = theme_colors.get("primary", "#1F4E79").lstrip("#")
    try:
        r, g, b = int(primary[0:2], 16), int(primary[2:4], 16), int(primary[4:6], 16)
    except (ValueError, IndexError):
        return "blue"

    delta = max(r, g, b) - min(r, g, b)
    lightness = (max(r, g, b) + min(r, g, b)) / 2 / 255

    if delta < 35:
        if lightness > 0.85:
            return "white"
        elif lightness < 0.25:
            return "dark"
        else:
            return "gray" if lightness < 0.6 else "neutral"

    if max(r, g, b) == r:
        return "orange" if g > 120 else "red" if g < 90 else "pink"
    elif max(r, g, b) == g:
        return "green"
    else:
        return "blue"


def _infer_layout(zones: list[dict], slide_index: int) -> str:
    """Infer slide layout type from its zones."""
    types = [z.get("type", "") for z in zones]
    has_title = "title" in types
    has_image = "image" in types
    has_body = any(t in ("body", "bullets", "chart") for t in types)
    has_columns = sum(1 for t in types if t in ("body", "bullets")) >= 2

    if slide_index == 0 and has_title:
        return "cover"
    if has_image and not has_body:
        return "full_image"
    if has_columns:
        return "two_column"
    if has_image and has_body:
        return "content_with_image"
    if has_title and has_body:
        return "content"
    return "content"


# ── CLI ───────────────────────────────────────────────────────────────


def main():
    """CLI entry point: ``python -m ppt_agent.templates.ingest <file.pptx>``."""
    import argparse
    parser = argparse.ArgumentParser(
        description="Auto-ingest a PPTX template — one click, no manual params",
    )
    parser.add_argument("pptx", help="Path to the .pptx file")
    parser.add_argument("--force", action="store_true", help="Overwrite existing template")
    parser.add_argument("--templates-root", default=None, help="Templates directory (default: ./templates)")
    args = parser.parse_args()

    root = Path(args.templates_root) if args.templates_root else None

    try:
        entry = auto_ingest(args.pptx, templates_root=root, force=args.force)
        print(f"\n[OK] Template ingested successfully!")
        print(f"   ID:       {entry['template_id']}")
        print(f"   Domain:   {entry['domain']}")
        print(f"   Style:    {entry['style']}")
        print(f"   Scene:    {entry['scene']}")
        print(f"   Slides:   {entry['slide_count']}")
        print(f"   Color:    {entry['color_scheme']}")
        print(f"   Location: templates/{entry['path']}")
        print(f"\n   No index.json edit needed -- auto-discovery will pick it up.")
    except FileExistsError as e:
        print(f"[FAIL] {e}")
        return 1
    except Exception as e:
        print(f"[FAIL] Error: {e}")
        logger.exception("Ingestion failed")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
