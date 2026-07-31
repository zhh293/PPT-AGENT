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

import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
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

        # XML shapes are the only writable zones. OCR is audit evidence only.
        merged_zones = _merge_zone_sources(zones, pptx_zones)
        baked_text_regions = _unmatched_ocr_zones(zones, pptx_zones)

        slide_meta = {
            "index": idx,
            "image_path": str(image_path.relative_to(template_dir)) if image_path and image_path.exists() else None,
            "zones": merged_zones,
            "ocr_text": ocr_text,
            "baked_text_regions": baked_text_regions,
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
        "meta_schema_version": "2.0",
        "parser_strategy": "pptx_xml_recursive",
        "template_sha256": hashlib.sha256(pptx_path.read_bytes()).hexdigest(),
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


def _find_tool(path: str) -> str | None:
    """Check if a tool exists at the given path."""
    p = Path(path)
    if p.exists():
        return str(p)
    return None


def _export_slides_to_images(
    pptx_path: Path,
    output_dir: Path,
    slide_count: int,
) -> dict[int, Path]:
    """Export PPTX slides to PNG images.

    Tries LibreOffice (high fidelity) first, falls back to user-provided
    images or empty dict.
    """
    soffice = (
        shutil.which("libreoffice")
        or shutil.which("soffice")
        # Windows default installation paths
        or _find_tool("C:\\Program Files\\LibreOffice\\program\\soffice.exe")
        or _find_tool("soffice.exe")  # Try PATH variants
    )

    if soffice:
        return _export_via_libreoffice(pptx_path, output_dir, soffice, slide_count)

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
    slide_count: int,
) -> dict[int, Path]:
    """Export PPTX slides to per-slide PNG.

    Pipeline: PPTX → PDF (LibreOffice) → per-slide PNG (PyMuPDF).
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pdf_path = tmp_path / f"{pptx_path.stem}.pdf"

        # Step 1: PPTX → PDF via LibreOffice.
        # Use shell mode on Windows — LibreOffice often needs the full
        # environment context (HOME, USERPROFILE, etc.) to render fonts.
        soffice_cmd = (
            f'"{soffice_bin}" --headless --convert-to pdf'
            f' --outdir "{tmp_path}" "{pptx_path}"'
        )
        try:
            subprocess.run(
                soffice_cmd, shell=True,
                capture_output=True, text=True, timeout=600,
                check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning("LibreOffice PDF conversion failed: %s", e)
            return {}

        if not pdf_path.exists():
            logger.warning("LibreOffice produced no PDF output at %s", pdf_path)
            return {}

        # Step 2: PDF → per-slide PNG via PyMuPDF
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(str(pdf_path))
            result: dict[int, Path] = {}

            for idx in range(len(doc)):
                page = doc[idx]
                # Render at 150 DPI (1920px wide for 16:9)
                mat = fitz.Matrix(150 / 72, 150 / 72)
                pix = page.get_pixmap(matrix=mat)
                out_path = output_dir / f"slide_{idx:02d}.png"
                pix.save(str(out_path))
                result[idx] = out_path

            doc.close()
            logger.info("Exported %d slide images to %s", len(result), output_dir)
            return result

        except ImportError:
            logger.warning("PyMuPDF (fitz) not installed — cannot convert PDF to PNG")
            return {}
        except Exception as e:
            logger.warning("PDF→PNG conversion failed: %s", e, exc_info=True)
            return {}


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


def _is_template_noise(text: str, x: float, y: float, w: float, h: float) -> bool:
    """Return True if the shape text looks like template documentation, not a content slot.

    Filters out: designer credits, font samples, color labels, template instructions,
    short labels in footer areas, and shapes that are too tiny to hold real content.
    """
    if not text:
        return False

    # ── Size-based: too small to hold readable content ──
    if w < 0.02 or h < 0.01:
        return True

    # ── Footer / corner credits ──
    # "PPT设计|刘万钊" pattern (designer credit, usually at bottom-right or top-right)
    credit_keywords = ["PPT设计", "PPT制作", "作者", "设计师", "版权所有", "模板来源"]
    for kw in credit_keywords:
        if kw in text:
            return True

    # ── Font / typography samples ──
    # "OPPOSans R", "思源黑体 Bold", etc. — short, often ALL_CAPS or font names
    font_keywords = ["OPPOSans", "Sans", "Bold", "Regular", "Medium", "Light"]
    # Check if text is purely a font name (short, pattern like "XXX Sans X")
    if len(text) < 20:
        for kw in font_keywords:
            if kw.lower() in text.lower():
                return True

    # ── Color labels ──
    # Single color names that are too short to be real content
    color_names = {"红", "橙", "黄", "绿", "蓝", "紫", "黑", "白", "灰", "棕",
                   "强调色", "主色", "辅色", "暗红色", "深蓝色", "浅灰色", "中性色",
                   "标题", "正文", "副标题", "页面标题"}
    stripped = text.strip()
    if stripped in color_names:
        return True

    # ── Template instructions ──
    # Templates often contain usage notes
    instruction_patterns = [
        "注意：", "注意:", "请不要", "不要直接", "需要加粗", "建议使用",
        "点击此处", "单击此处", "请输入", "在此输入",
        "选取了", "该模板", "本模板", "使用方法", "使用说明",
        "可替换", "可直接", "可自行", "可更改",
    ]
    for pat in instruction_patterns:
        if pat in text:
            return True

    # ── Short text in footer zone (>80% down) ──
    if y > 0.80 and len(text) < 30:
        return True

    # ── Single short word repeated (like section numbers) ──
    if len(text) <= 3 and text.isdigit():
        return True

    return False


def _zones_from_pptx_shapes_legacy(slide, slide_index: int,
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

        # ── Clamp to valid slide bounds ──
        x = max(0.0, min(1.0, x))
        y = max(0.0, min(1.0, y))
        w = max(0.0, min(1.0 - x, w))
        h = max(0.0, min(1.0 - y, h))

        # ── Skip full-slide shapes (backgrounds, overlays) ──
        if w > 0.95 and h > 0.95:
            continue

        # ── Skip tiny shapes (decorative dots, invisible elements) ──
        if w < 0.01 and h < 0.01:
            continue

        # Determine zone type
        zone_type = "decoration"
        text_content = ""

        if shape.has_text_frame:
            texts = [p.text for p in shape.text_frame.paragraphs if p.text.strip()]
            text_content = "\n".join(texts)

            # ── Skip obvious template noise ──
            if _is_template_noise(text_content, x, y, w, h):
                continue  # Don't add this shape as a zone

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
                # ── Non-placeholder text box with actual text ──
                # Classify by position AND area rather than strict y/w
                # thresholds that miss edge cases (e.g. full-image slides
                # where text is placed outside the conventional y<0.25 zone).
                area = w * h
                if area < 0.002:
                    # Too tiny to hold readable text → skip
                    continue
                if y < 0.35 and area > 0.003:
                    zone_type = "title"
                elif y > 0.80 and w < 0.95:
                    zone_type = "footer"
                else:
                    zone_type = "body"

        elif hasattr(shape, "image"):
            try:
                _ = shape.image
                zone_type = "image"
            except Exception:
                zone_type = "decoration"

        # ── Extract formatting (font, size, color) from the shape ──
        formatting = _extract_shape_formatting(shape) if shape.has_text_frame else {}

        # ── Refine zone type using font size ──
        zone_type = _refine_zone_type(zone_type, formatting, y, w, h)

        native_shape_id = getattr(shape, "shape_id", i)
        shape_path = f"slide-{slide_index + 1}/shape-{native_shape_id}"
        geometry_payload = ":".join(
            str(value) for value in (shape.left, shape.top, shape.width, shape.height)
        )
        formatting_payload = json.dumps(formatting, sort_keys=True, ensure_ascii=False)

        zones.append({
            "zone_id": shape_path,
            "legacy_zone_id": f"s{slide_index}_shape{i}",
            "native_shape_id": native_shape_id,
            "shape_path": shape_path,
            "shape_name": getattr(shape, "name", ""),
            "type": zone_type,
            "position": [round(x, 4), round(y, 4), round(w, 4), round(h, 4)],
            "text": text_content,
            "original_text": text_content,
            "editable": bool(shape.has_text_frame),
            "formatting": formatting,
            "geometry_fingerprint": hashlib.sha256(
                geometry_payload.encode("utf-8")
            ).hexdigest()[:16],
            "formatting_fingerprint": hashlib.sha256(
                formatting_payload.encode("utf-8")
            ).hexdigest()[:16],
            "confidence": 0.85 if text_content else 0.65,
            "source": "pptx_shape",
        })

    return zones


def _group_child_transform(group, displayed: tuple[float, float, float, float]):
    """Return an affine transform from group child coordinates to the slide."""
    gx, gy, gw, gh = displayed
    try:
        xfrm = group._element.grpSpPr.xfrm
        ch_off_x = float(xfrm.chOff.x)
        ch_off_y = float(xfrm.chOff.y)
        ch_ext_x = float(xfrm.chExt.cx) or 1.0
        ch_ext_y = float(xfrm.chExt.cy) or 1.0
    except Exception:
        ch_off_x = float(group.left or 0)
        ch_off_y = float(group.top or 0)
        ch_ext_x = float(group.width or 1)
        ch_ext_y = float(group.height or 1)
    scale_x = gw / ch_ext_x
    scale_y = gh / ch_ext_y
    return (
        gx - ch_off_x * scale_x,
        gy - ch_off_y * scale_y,
        scale_x,
        scale_y,
    )


def _iter_shapes_recursive(shapes, slide_index: int):
    """Yield all leaf shapes with group-aware identity and displayed geometry."""

    def walk(collection, parent_ids, prefix, transform, parent_rotation, depth):
        offset_x, offset_y, scale_x, scale_y = transform
        for ordinal, shape in enumerate(collection):
            raw_x = float(shape.left or 0)
            raw_y = float(shape.top or 0)
            raw_w = float(shape.width or 0)
            raw_h = float(shape.height or 0)
            displayed = (
                offset_x + raw_x * scale_x,
                offset_y + raw_y * scale_y,
                raw_w * scale_x,
                raw_h * scale_y,
            )
            native_id = int(getattr(shape, "shape_id", ordinal))
            rotation = float(getattr(shape, "rotation", 0.0) or 0.0)
            if getattr(shape, "shape_type", None) == MSO_SHAPE_TYPE.GROUP:
                yield from walk(
                    shape.shapes,
                    [*parent_ids, native_id],
                    f"{prefix}/group-{native_id}",
                    _group_child_transform(shape, displayed),
                    (parent_rotation + rotation) % 360,
                    depth + 1,
                )
                continue
            yield {
                "shape": shape,
                "shape_path": f"{prefix}/shape-{native_id}",
                "parent_group_ids": parent_ids,
                "shape_depth": depth,
                "displayed_geometry": displayed,
                "parent_rotation": parent_rotation,
            }

    yield from walk(
        shapes, [], f"slide-{slide_index + 1}",
        (0.0, 0.0, 1.0, 1.0), 0.0, 0,
    )


def _text_direction(shape, parent_rotation: float) -> dict:
    shape_rotation = float(getattr(shape, "rotation", 0.0) or 0.0) % 360
    text_rotation = 0.0
    vertical_mode = "horizontal"
    try:
        body_pr = shape.text_frame._txBody.bodyPr
        vertical_attr = body_pr.get("vert")
        if vertical_attr and vertical_attr != "horz":
            vertical_mode = vertical_attr
        raw_text_rotation = body_pr.get("rot")
        if raw_text_rotation:
            text_rotation = float(raw_text_rotation) / 60000.0
    except Exception:
        pass
    effective = (parent_rotation + shape_rotation + text_rotation) % 360
    return {
        "shape_rotation_deg": round(shape_rotation, 3),
        "text_rotation_deg": round(text_rotation, 3),
        "parent_rotation_deg": round(parent_rotation % 360, 3),
        "effective_rotation_deg": round(effective, 3),
        "vertical_mode": vertical_mode,
    }


def _content_constraints(zone_type: str, position: list[float],
                         direction: dict, is_noise: bool) -> dict:
    _x, _y, width, height = position
    aspect_ratio = width / height if height else 0.0
    effective = direction["effective_rotation_deg"]
    rotated = min(abs(effective - 90), abs(effective - 270)) <= 10
    vertical = direction["vertical_mode"] != "horizontal" or rotated
    narrow = width < 0.08 or aspect_ratio < 0.4
    if is_noise or zone_type == "footer":
        eligibility = "decorative"
    elif vertical or narrow:
        eligibility = "short_label"
    elif zone_type in ("title", "subtitle"):
        eligibility = "title"
    else:
        eligibility = "body"
    max_lines = max(1, int(height / 0.035))
    max_chars = min(600, max_lines * max(2, int(width / 0.018)))
    if eligibility == "short_label":
        max_chars = min(max_chars, 12)
    return {
        "content_eligibility": eligibility,
        "supports_long_text": eligibility == "body" and max_chars >= 40,
        "max_chars_hint": max_chars,
        "max_lines_hint": max_lines,
        "aspect_ratio": round(aspect_ratio, 3),
        "is_narrow": narrow,
    }


def _zones_from_pptx_shapes(slide, slide_index: int,
                            slide_w: int | None = None,
                            slide_h: int | None = None) -> list[dict]:
    """Build zones exclusively from the complete native PPTX/XML shape tree."""
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
    for item_index, item in enumerate(_iter_shapes_recursive(slide.shapes, slide_index)):
        shape = item["shape"]
        left, top, width, height = item["displayed_geometry"]
        x = max(0.0, min(1.0, left / slide_w if left else 0.0))
        y = max(0.0, min(1.0, top / slide_h if top else 0.0))
        width_n = max(0.0, min(1.0 - x, width / slide_w if width else 0.0))
        height_n = max(0.0, min(1.0 - y, height / slide_h if height else 0.0))
        has_text = bool(getattr(shape, "has_text_frame", False))
        has_picture_fill = bool(
            shape._element.xpath('.//*[local-name()="blip"]')
        )
        if width_n > 0.95 and height_n > 0.95 and not has_text:
            continue
        if width_n < 0.01 and height_n < 0.01 and not has_text:
            continue

        zone_type = "decorative"
        text_content = ""
        is_placeholder = False
        if has_text:
            text_content = "\n".join(
                paragraph.text for paragraph in shape.text_frame.paragraphs
                if paragraph.text.strip()
            )
            if has_picture_fill and not text_content:
                zone_type = "image"
            else:
                try:
                    placeholder = shape.placeholder_format
                except ValueError:
                    placeholder = None
                if placeholder is not None:
                    is_placeholder = True
                    placeholder_type = placeholder.type
                    if placeholder_type in (1, 3, 15):
                        zone_type = "title"
                    elif placeholder_type in (2, 4):
                        zone_type = "subtitle"
                    else:
                        zone_type = "body"
                else:
                    area = width_n * height_n
                    if area < 0.002:
                        zone_type = "decorative"
                    elif y < 0.35 and area > 0.003:
                        zone_type = "title"
                    elif y > 0.80 and width_n < 0.95:
                        zone_type = "footer"
                    else:
                        zone_type = "body"
        elif hasattr(shape, "image"):
            try:
                _ = shape.image
                zone_type = "image"
            except Exception:
                pass

        is_text_box = getattr(shape, "shape_type", None) == MSO_SHAPE_TYPE.TEXT_BOX
        editable = (
            has_text
            and zone_type != "image"
            and bool(text_content or is_placeholder or is_text_box)
        )
        if not editable and zone_type not in ("image", "chart"):
            continue

        formatting = _extract_shape_formatting(shape) if editable else {}
        zone_type = _refine_zone_type(
            zone_type, formatting, y, width_n, height_n
        )
        position = [
            round(x, 4), round(y, 4), round(width_n, 4), round(height_n, 4)
        ]
        is_noise = editable and _is_template_noise(
            text_content, x, y, width_n, height_n
        )
        direction = _text_direction(shape, item["parent_rotation"])
        constraints = (
            _content_constraints(zone_type, position, direction, is_noise)
            if editable else {}
        )
        native_shape_id = int(getattr(shape, "shape_id", item_index))
        geometry_payload = ":".join(
            str(round(value, 3)) for value in (left, top, width, height)
        )
        formatting_payload = json.dumps(
            formatting, sort_keys=True, ensure_ascii=False
        )
        zones.append({
            "zone_id": item["shape_path"],
            "legacy_zone_id": f"s{slide_index}_shape{item_index}",
            "native_shape_id": native_shape_id,
            "shape_path": item["shape_path"],
            "shape_name": getattr(shape, "name", ""),
            "shape_type": str(getattr(shape, "shape_type", "unknown")),
            "shape_depth": item["shape_depth"],
            "parent_group_ids": item["parent_group_ids"],
            "type": zone_type,
            "position": position,
            "text": text_content,
            "original_text": text_content,
            "editable": editable,
            "semantic_role": "template_noise" if is_noise else zone_type,
            "formatting": formatting,
            **direction,
            **constraints,
            "geometry_fingerprint": hashlib.sha256(
                geometry_payload.encode("utf-8")
            ).hexdigest()[:16],
            "formatting_fingerprint": hashlib.sha256(
                formatting_payload.encode("utf-8")
            ).hexdigest()[:16],
            "confidence": 1.0,
            "source": "pptx_xml",
        })
    return zones


def _refine_zone_type(zone_type: str, fmt: dict,
                      y: float, w: float, h: float) -> str:
    """Improve zone type classification using font size and position.

    Uses actual typography data to correct heuristic misclassifications:
    - 20pt+ font with large width → almost certainly a title
    - 10pt- font → likely body text, not a title
    - Narrow shapes with small font → decoration, not content
    """
    font_pt = fmt.get("font_size_pt")
    font_name = fmt.get("font_name", "")

    if font_pt is None:
        # Font data may be inherited from slide master.  Don't demote the
        # zone type — the original classification (from placeholder type or
        # position heuristic) is more trustworthy than the absence of
        # explicit font data.
        return zone_type

    # ── Strong title signals ──
    if font_pt >= 20 and w > 0.10 and zone_type != "image":
        return "title"

    # ── Subtitle signals: medium font, below typical title area ──
    if font_pt >= 16 and y > 0.15 and zone_type == "title":
        return "subtitle"

    # ── Body signals: small font ──
    if font_pt <= 12 and zone_type == "title":
        return "body"

    # ── Decoration / label: very small font, narrow shape ──
    if font_pt < 10 and w < 0.10:
        return "decoration"

    # ── Footer: small font at bottom ──
    if font_pt < 12 and y > 0.85:
        return "footer"

    return zone_type


def _extract_shape_formatting(shape) -> dict:
    """Extract font, size, color, and alignment from a shape's first paragraph.

    Returns a dict with keys: font_name, font_size_pt, font_color, fill_color,
    bold, italic, alignment.  Values may be None if inherited from master/layout.
    """
    fmt: dict = {
        "font_name": None,
        "font_size_pt": None,
        "font_color": None,
        "fill_color": None,
        "bold": None,
        "italic": None,
        "alignment": None,
        "line_spacing": None,
    }

    # ── Fill color (shape background) ──
    try:
        fill = shape.fill
        if fill.type is not None and hasattr(fill, 'fore_color'):
            if fill.fore_color.type is not None:
                fmt["fill_color"] = str(fill.fore_color.rgb)
    except Exception:
        pass

    if not shape.has_text_frame:
        return fmt

    tf = shape.text_frame

    # ── Paragraph-level formatting (first paragraph) ──
    if tf.paragraphs:
        p = tf.paragraphs[0]

        # Alignment
        if p.alignment is not None:
            align_map = {0: "LEFT", 1: "CENTER", 2: "RIGHT", 3: "JUSTIFY"}
            fmt["alignment"] = align_map.get(p.alignment, "LEFT")

        # Line spacing
        if p.line_spacing is not None:
            fmt["line_spacing"] = p.line_spacing / 12700  # EMU → pt

        # Run-level formatting (first run)
        if p.runs:
            r = p.runs[0]

            if r.font.name:
                fmt["font_name"] = r.font.name
            if r.font.size:
                fmt["font_size_pt"] = round(r.font.size / 12700, 1)
            if r.font.bold is not None:
                fmt["bold"] = r.font.bold
            if r.font.italic is not None:
                fmt["italic"] = r.font.italic
            try:
                if r.font.color and r.font.color.type is not None:
                    fmt["font_color"] = str(r.font.color.rgb)
            except Exception:
                pass

        # Fallback: if run-level font size is None, try paragraph-level
        if fmt["font_size_pt"] is None and p.font.size:
            fmt["font_size_pt"] = round(p.font.size / 12700, 1)

    # ── Fallback: try other runs if first didn't have font data ──
    if fmt["font_name"] is None:
        for p in tf.paragraphs:
            for r in p.runs:
                if r.font.name:
                    fmt["font_name"] = r.font.name
                    break
            if fmt["font_name"]:
                break

    if fmt["font_size_pt"] is None:
        for p in tf.paragraphs:
            for r in p.runs:
                if r.font.size:
                    fmt["font_size_pt"] = round(r.font.size / 12700, 1)
                    break
            if fmt["font_size_pt"]:
                break

    if fmt["font_color"] is None:
        for p in tf.paragraphs:
            for r in p.runs:
                try:
                    if r.font.color and r.font.color.type is not None:
                        fmt["font_color"] = str(r.font.color.rgb)
                        break
                except Exception:
                    pass
            if fmt["font_color"]:
                break

    # ── Last resort: try paragraph-level (may expose inherited formatting
    #     that run-level font does not) ──
    if fmt["font_name"] is None:
        for p in tf.paragraphs:
            if p.font.name:
                fmt["font_name"] = p.font.name
                break
    if fmt["font_size_pt"] is None:
        for p in tf.paragraphs:
            if p.font.size:
                fmt["font_size_pt"] = round(p.font.size / 12700, 1)
                break
    if fmt["font_color"] is None:
        for p in tf.paragraphs:
            try:
                if p.font.color and p.font.color.type is not None:
                    fmt["font_color"] = str(p.font.color.rgb)
                    break
            except Exception:
                pass

    return fmt


def _merge_zone_sources(
    ocr_zones: list[dict],
    shape_zones: list[dict],
) -> list[dict]:
    """Annotate XML zones from OCR without creating writable OCR zones."""
    if not ocr_zones or not shape_zones:
        return shape_zones

    merged = list(shape_zones)  # Start with shape zones (structural truth)

    # OCR may confirm rendered text, but it never becomes an execution address.
    for ocr_z in ocr_zones:
        overlaps = False
        for shape_z in shape_zones:
            if _zones_overlap(ocr_z["position"], shape_z["position"], threshold=0.3):
                # Supplement the shape zone with OCR text if it has no text
                if not shape_z.get("text") and ocr_z.get("text"):
                    shape_z["ocr_text"] = ocr_z["text"]
                overlaps = True
                break
    return merged


def _unmatched_ocr_zones(ocr_zones: list[dict], shape_zones: list[dict]) -> list[dict]:
    """Return visible OCR regions that cannot be addressed as PPTX text shapes."""
    unmatched: list[dict] = []
    for zone in ocr_zones:
        if any(
            _zones_overlap(zone.get("position", [0, 0, 0, 0]),
                           shape.get("position", [0, 0, 0, 0]), threshold=0.3)
            for shape in shape_zones if shape.get("editable")
        ):
            continue
        unmatched.append({
            "position": zone.get("position", [0, 0, 0, 0]),
            "text": zone.get("text", ""),
            "source": "ocr_audit",
            "editable": False,
        })
    return unmatched


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


def refresh_template_meta(template_dir: str | Path, *, run_ocr: bool = False) -> dict:
    """Safely rebuild ``meta.json`` for an existing template in place.

    Unlike ``auto_ingest(..., force=True)``, this operation never deletes the
    template directory. Native PPTX/XML zones are always rebuilt; OCR is an
    optional rendered-text audit and cannot create editable zones.
    """
    template_dir = Path(template_dir).resolve()
    pptx_path = template_dir / "template.pptx"
    meta_path = template_dir / "meta.json"
    if not pptx_path.exists():
        raise FileNotFoundError(f"Template PPTX not found: {pptx_path}")
    previous = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    previous_slides = {
        int(slide.get("index", index)): slide
        for index, slide in enumerate(previous.get("slides", []))
    }
    presentation = Presentation(str(pptx_path))
    slides_meta: list[dict] = []
    for index, slide in enumerate(presentation.slides):
        old_slide = previous_slides.get(index, {})
        shape_zones = _zones_from_pptx_shapes(
            slide, index, presentation.slide_width, presentation.slide_height
        )
        ocr_text = old_slide.get("ocr_text", "")
        baked_text_regions = old_slide.get("baked_text_regions", [])
        if run_ocr:
            candidates = []
            if old_slide.get("image_path"):
                candidates.append(template_dir / old_slide["image_path"])
            candidates.extend([
                template_dir / "preview" / f"slide_{index:02d}.png",
                template_dir / "slides" / f"slide_{index:02d}.png",
            ])
            image_path = next((path for path in candidates if path.exists()), None)
            if image_path is not None:
                regions = ocr_slide_image(image_path)
                ocr_zones = classify_zones(regions, slide_index=index)
                ocr_text = " ".join(region.text for region in regions)
                shape_zones = _merge_zone_sources(ocr_zones, shape_zones)
                baked_text_regions = _unmatched_ocr_zones(ocr_zones, shape_zones)
        slides_meta.append({
            **old_slide,
            "index": index,
            "layout": _infer_layout(shape_zones, index),
            "zones": shape_zones,
            "ocr_text": ocr_text,
            "baked_text_regions": baked_text_regions,
        })

    refreshed = {
        **previous,
        "meta_schema_version": "2.0",
        "parser_strategy": "pptx_xml_recursive",
        "template_sha256": hashlib.sha256(pptx_path.read_bytes()).hexdigest(),
        "slide_count": len(slides_meta),
        "slides": slides_meta,
    }
    if not refreshed.get("template_id"):
        refreshed["template_id"] = template_dir.name
    if not refreshed.get("style"):
        refreshed["style"] = "PPTX template"
    meta_path.write_text(
        json.dumps(refreshed, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return refreshed


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
        baked_text_regions: list[dict] = []
        if image_path and image_path.exists():
            try:
                regions = ocr_slide_image(image_path)
                ocr_zones = classify_zones(regions, slide_index=idx)
                ocr_text = " ".join(r.text for r in regions)
                all_ocr_text.append(ocr_text)
                merged_zones = _merge_zone_sources(ocr_zones, shape_zones)
                baked_text_regions = _unmatched_ocr_zones(ocr_zones, shape_zones)
            except Exception:
                merged_zones = shape_zones
        else:
            merged_zones = shape_zones

        # ── Visual refinement: use pixel data to correct zone types ──
        try:
            from ppt_agent.vision.zone_refiner import refine_zones_with_vision
            merged_zones = refine_zones_with_vision(
                merged_zones,
                image_path if image_path and image_path.exists() else None,
                slide_index=idx,
            )
        except Exception:
            pass  # Visual refinement is best-effort

        # Infer layout from zone pattern
        layout = _infer_layout(merged_zones, idx)

        slides_meta.append({
            "index": idx,
            "layout": layout,
            "zones": merged_zones,
            "ocr_text": ocr_text,
            "baked_text_regions": baked_text_regions,
        })

    # ── Write meta.json ────────────────────────────────────────────
    meta = {
        "meta_schema_version": "2.0",
        "parser_strategy": "pptx_xml_recursive",
        "template_sha256": hashlib.sha256(pptx_path.read_bytes()).hexdigest(),
        "template_id": template_id,
        "domain": domain,
        "scene": scene,
        "style": style,
        "slide_count": slide_count,
        "color_scheme": theme_colors,
        "slides": slides_meta,
    }

    # ── Run visual audit on the template PPTX (structural, no images needed) ──
    try:
        from ppt_agent.vision.pptx_audit import audit_pptx as _visual_audit
        audit_report = _visual_audit(pptx_path)
        meta["design_audit"] = {
            "aggregate_score": round(audit_report.aggregate_score, 1),
            "font_issues": audit_report.total_font_issues,
            "overflow_issues": audit_report.total_overflow_issues,
            "contrast_issues": audit_report.total_contrast_issues,
            "recommendations": audit_report.recommendations,
        }
        logger.info("Visual audit score: %.1f", audit_report.aggregate_score)
    except Exception as exc:
        logger.debug("Visual audit skipped during ingest: %s", exc)

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
