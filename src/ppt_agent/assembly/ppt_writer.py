"""PPT Writer — generate PPTX with background images + text overlay.

The new assembly model:
1. Each slide gets a FULL-PAGE background image (AI-generated via img2img)
2. Text zones are overlaid on top as transparent text boxes
3. Text positions come from the template's OCR/shape analysis

When no background image is available for a slide, falls back to a
solid-color background with the old zone-based layout.

This produces PPTs where:
- The visual design is an AI-generated full-page image (gorgeous backgrounds)
- The text content is editable (real text boxes, not baked into the image)
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from ppt_agent.assembly.layout_fit import bullet_font_size, title_font_size

logger = logging.getLogger(__name__)

SLIDE_W = 12192000  # EMU – 16:9 width
SLIDE_H = 6858000   # EMU – 16:9 height


def _emu_rect(position: list[float]) -> tuple[int, int, int, int]:
    """Convert fractional [x, y, w, h] to EMU (left, top, width, height)."""
    x_frac, y_frac, w_frac, h_frac = position
    return (
        int(x_frac * SLIDE_W),
        int(y_frac * SLIDE_H),
        int(w_frac * SLIDE_W),
        int(h_frac * SLIDE_H),
    )


def _resolve_image_path(image_path: str, workspace_root: Path | None) -> Path | None:
    """Resolve an image path; return *None* when the file cannot be found."""
    p = Path(image_path)
    if p.is_absolute() and p.exists():
        return p
    if workspace_root is not None:
        resolved = workspace_root / image_path
        if resolved.exists():
            return resolved
    if p.exists():
        return p
    return None


# ── Background image mode (new) ──

def _set_background_image(slide, image_path: Path) -> None:
    """Add a full-page background image to the slide.

    The image is placed at (0, 0) with full slide dimensions,
    sitting behind all other shapes.
    """
    slide.shapes.add_picture(
        str(image_path),
        Emu(0), Emu(0),
        Emu(SLIDE_W), Emu(SLIDE_H),
    )


def _shorten_background_copy(text: str, zone: dict) -> str:
    """Fit outline copy to a visual slot without slicing words or metrics."""
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        return ""
    position = zone.get("position", [0, 0, 0, 0])
    formatting = zone.get("formatting", {}) or {}
    font_pt = min(max(float(formatting.get("font_size_pt") or 16), 14), 20)
    chars_per_line = max(5, int(float(position[2]) * 650 / font_pt))
    max_lines = max(1, int(float(position[3]) * 900 / font_pt))
    allowed_lines = 1 if float(position[3]) < 0.12 else min(max_lines, 2)
    capacity = max(4, int(chars_per_line * allowed_lines * 0.75))
    if len(re.sub(r"\s+", "", cleaned)) <= capacity:
        return cleaned

    without_parenthetical = re.sub(r"\s*[\(（][^()（）]*[\)）]\s*", "", cleaned)
    clauses = [
        clause.strip()
        for clause in re.split(r"[。；;，,]|→", without_parenthetical)
        if clause.strip()
    ]
    result = ""
    for clause in clauses:
        candidate = clause if not result else f"；{clause}"
        if len(re.sub(r"\s+", "", result + candidate)) <= capacity:
            result += candidate
    if result:
        return result
    return min(clauses or [without_parenthetical], key=lambda value: len(value))


def _outline_background_text_zones(
    slide_data: dict,
    outline_slide: dict | None,
) -> list[dict]:
    """Create a sparse, non-overlapping text layer for a full-page visual."""
    if not outline_slide:
        return []
    zones = [
        zone for zone in slide_data.get("zones", [])
        if zone.get("type") in ("title", "subtitle", "body", "bullets", "footer")
        and len(zone.get("position", [])) >= 4
    ]
    if not zones:
        return []

    title_candidates = [
        zone for zone in zones
        if zone.get("type") in ("title", "subtitle")
        and float(zone["position"][1]) < 0.22
        and float(zone["position"][2]) >= 0.30
    ]
    if title_candidates:
        title_zone = max(
            title_candidates,
            key=lambda zone: (
                float(zone["position"][2]),
                -float(zone["position"][1]),
            ),
        )
    else:
        title_zone = {
            "zone_id": "_generated_background_title",
            "type": "title",
            "position": [0.15, 0.05, 0.77, 0.10],
            "formatting": {
                "font_size_pt": 30,
                "font_color": "17324D",
                "bold": True,
                "alignment": "LEFT",
            },
            "action": "replace_text",
        }
    title_overlay = dict(title_zone)
    title_overlay["content"] = outline_slide.get("title", "")
    title_overlay["type"] = "title"
    title_overlay["formatting"] = dict(
        title_overlay.get("formatting", {}) or {}
    )
    title_overlay["formatting"]["font_size_pt"] = max(
        28, float(title_overlay["formatting"].get("font_size_pt") or 0)
    )

    bullets = [
        str(bullet).strip()
        for bullet in outline_slide.get("bullets", [])
        if str(bullet).strip()
    ]

    def preset_overlays(positions: list[list[float]], font_size: int = 16) -> list[dict]:
        result = [title_overlay]
        for index, (position, bullet) in enumerate(zip(positions, bullets)):
            zone = {
                "zone_id": f"_generated_background_body_{index}",
                "type": "body",
                "position": position,
                "action": "replace_text",
                "formatting": {
                    "font_size_pt": font_size,
                    "font_color": "17324D",
                    "alignment": "CENTER",
                },
            }
            zone["content"] = _shorten_background_copy(bullet, zone)
            result.append(zone)
        return result

    # Generated backgrounds use a small set of intentional composition
    # patterns. Semantic slots are safer than borrowing coordinates from the
    # source template because the new visual may use a different composition.
    slide_type = str(outline_slide.get("type") or "").strip().lower()
    slide_index = int(outline_slide.get("slide_index", slide_data.get("slide_index", -1)))
    if slide_type == "background":
        return preset_overlays(
            [
                [0.20, 0.475, 0.68, 0.055],
                [0.20, 0.545, 0.68, 0.055],
                [0.20, 0.615, 0.68, 0.055],
                [0.20, 0.685, 0.68, 0.055],
            ],
            font_size=15,
        )
    if slide_type == "innovation":
        return preset_overlays(
            [
                [0.57, 0.270, 0.31, 0.065],
                [0.57, 0.370, 0.31, 0.065],
                [0.57, 0.470, 0.31, 0.065],
                [0.57, 0.585, 0.31, 0.065],
                [0.57, 0.695, 0.31, 0.065],
            ],
            font_size=15,
        )
    if slide_type == "feature_demo" and slide_index == 7:
        return preset_overlays(
            [
                [0.20, 0.255, 0.25, 0.075],
                [0.66, 0.255, 0.25, 0.075],
            ],
            font_size=16,
        )
    if slide_type == "value":
        return preset_overlays(
            [
                [0.11, 0.355, 0.14, 0.09],
                [0.27, 0.355, 0.14, 0.09],
                [0.43, 0.355, 0.14, 0.09],
                [0.59, 0.355, 0.14, 0.09],
            ],
            font_size=14,
        )
    if slide_type == "roadmap":
        return preset_overlays(
            [
                [0.08, 0.31, 0.25, 0.10],
                [0.375, 0.31, 0.25, 0.10],
                [0.67, 0.31, 0.25, 0.10],
                [0.20, 0.765, 0.60, 0.08],
            ],
            font_size=16,
        )

    candidates = []
    for zone in zones:
        if zone is title_zone:
            continue
        position = zone["position"]
        if (
            float(position[1]) < 0.20
            or float(position[2]) < 0.10
            or float(position[3]) < 0.04
        ):
            continue
        candidates.append(zone)

    # Select the largest useful slots first while suppressing layered or
    # duplicate template shapes that occupy the same visual region.
    selected: list[dict] = []
    for zone in sorted(
        candidates,
        key=lambda item: float(item["position"][2]) * float(item["position"][3]),
        reverse=True,
    ):
        position = zone["position"]
        area = max(float(position[2]) * float(position[3]), 1e-9)
        overlaps = False
        for existing in selected:
            other = existing["position"]
            left = max(float(position[0]), float(other[0]))
            top = max(float(position[1]), float(other[1]))
            right = min(
                float(position[0]) + float(position[2]),
                float(other[0]) + float(other[2]),
            )
            bottom = min(
                float(position[1]) + float(position[3]),
                float(other[1]) + float(other[3]),
            )
            intersection = max(0.0, right - left) * max(0.0, bottom - top)
            other_area = max(float(other[2]) * float(other[3]), 1e-9)
            if intersection / min(area, other_area) >= 0.45:
                overlaps = True
                break
        if not overlaps:
            selected.append(zone)

    # Repeated vertical rows are a strong template signal (architecture layer
    # lists, metric rails, comparison criteria). Prefer that coherent column
    # when it can hold the complete bullet set.
    x_buckets: dict[float, list[dict]] = {}
    for zone in selected:
        x_buckets.setdefault(round(float(zone["position"][0]), 1), []).append(zone)
    column_candidates = [
        column for column in x_buckets.values()
        if len(column) >= len(bullets) and bullets
    ]
    y_buckets: dict[float, list[dict]] = {}
    for zone in selected:
        y_buckets.setdefault(round(float(zone["position"][1]), 1), []).append(zone)
    row_candidates = [
        row for row in y_buckets.values()
        if len(row) >= len(bullets) and bullets
    ]
    lower_band = [
        zone for zone in selected
        if float(zone["position"][1]) >= 0.55
    ]
    if column_candidates:
        selected = max(
            column_candidates,
            key=lambda column: (
                len(column),
                sum(
                    float(item["position"][2]) * float(item["position"][3])
                    for item in column
                ),
            ),
        )
    elif len(lower_band) >= len(bullets) and bullets:
        selected = lower_band
    elif row_candidates:
        selected = max(
            row_candidates,
            key=lambda row: (
                len(row),
                sum(
                    float(item["position"][2]) * float(item["position"][3])
                    for item in row
                ),
            ),
        )
    else:
        selected = selected[:len(bullets)]
    selected = sorted(
        selected[:len(bullets)],
        key=lambda item: (
            float(item["position"][1]),
            float(item["position"][0]),
        ),
    )
    overlays = [title_overlay]
    for zone, bullet in zip(selected, bullets):
        overlay = dict(zone)
        overlay["type"] = "body"
        overlay["formatting"] = dict(overlay.get("formatting", {}) or {})
        overlay["formatting"]["font_size_pt"] = min(
            max(float(overlay["formatting"].get("font_size_pt") or 16), 14),
            20,
        )
        overlay["content"] = _shorten_background_copy(bullet, overlay)
        overlays.append(overlay)
    return overlays


def _add_text_overlay(slide, zone: dict) -> None:
    """Add a transparent text box overlaid on the background image.

    The text box has no fill (transparent background) so the AI-generated
    background shows through.  Text color defaults to white for dark
    backgrounds and dark gray for light backgrounds.
    """
    zone_type = zone.get("type", "body")
    content = zone.get("content")
    position = zone.get("position", [0.1, 0.1, 0.8, 0.8])

    if not content:
        return

    # Normalize: content may be a list (e.g. LLM assigned bullets to a title zone)
    if isinstance(content, list):
        content = content[0] if len(content) == 1 else "\n".join(str(c) for c in content)

    left, top, width, height = _emu_rect(position)
    txbox = slide.shapes.add_textbox(Emu(left), Emu(top), Emu(width), Emu(height))
    tf = txbox.text_frame
    tf.word_wrap = True

    # Make text box background transparent
    txbox.fill.background()

    if zone_type == "title":
        text = str(content)
        p = tf.paragraphs[0]
        p.text = text
        p.alignment = PP_ALIGN.LEFT
        font = p.font
        # Template formatting with size fallback
        _apply_zone_formatting(p, zone, default_size_pt=title_font_size(text), default_color_hex="17324D")
        if not (zone.get("formatting", {}) or {}).get("font_size_pt"):
            font.size = Pt(title_font_size(text))
        elif font.size is not None and font.size.pt < 24:
            font.size = Pt(24)
        font.bold = True
        # Generated backgrounds reserve light text areas by contract. Use a
        # dark default when the template did not specify a color.
        if not (zone.get("formatting", {}) or {}).get("font_color"):
            font.color.rgb = RGBColor(0x17, 0x32, 0x4D)

    elif zone_type in ("bullets", "body"):
        items = content if isinstance(content, list) else [str(content)]
        font_sz = bullet_font_size(items)
        for idx, item in enumerate(items):
            if idx == 0:
                p = tf.paragraphs[0]
            else:
                p = tf.add_paragraph()
            p.text = item
            p.alignment = PP_ALIGN.LEFT
            p.level = 0

            pPr = p._pPr
            if pPr is None:
                pPr = p._p.get_or_add_pPr()
            for old in pPr.findall(qn("a:buNone")):
                pPr.remove(old)
            for old in pPr.findall(qn("a:buChar")):
                pPr.remove(old)
            if zone_type == "bullets":
                buChar = pPr.makeelement(qn("a:buChar"), {"char": "\u2022"})
                pPr.append(buChar)
            else:
                pPr.append(pPr.makeelement(qn("a:buNone"), {}))

            font = p.font
            _apply_zone_formatting(p, zone, default_size_pt=font_sz, default_color_hex="17324D")
            if not (zone.get("formatting", {}) or {}).get("font_size_pt"):
                font.size = Pt(font_sz)
            elif font.size is not None and font.size.pt < 14:
                font.size = Pt(14)
            if not (zone.get("formatting", {}) or {}).get("font_color"):
                font.color.rgb = RGBColor(0x17, 0x32, 0x4D)

    elif zone_type == "footer":
        text = str(content)
        p = tf.paragraphs[0]
        p.text = text
        p.alignment = PP_ALIGN.RIGHT
        _apply_zone_formatting(p, zone, default_size_pt=12, default_color_hex="52677A")

    elif zone_type == "subtitle":
        text = str(content)
        p = tf.paragraphs[0]
        p.text = text
        p.alignment = PP_ALIGN.LEFT
        _apply_zone_formatting(p, zone, default_size_pt=20, default_color_hex="17324D")


def _add_text_shadow(paragraph) -> None:
    """Add a subtle text shadow for legibility on image backgrounds.

    Uses the effectLst > outerShdw XML element for a drop shadow.
    """
    try:
        rPr = paragraph.runs[0]._r.get_or_add_rPr() if paragraph.runs else None
        if rPr is None:
            return

        # Create effectLst with outerShdw
        effectLst = rPr.makeelement(qn("a:effectLst"), {})
        outerShdw = effectLst.makeelement(qn("a:outerShdw"), {
            "blurRad": "38100",  # 3pt blur
            "dist": "19050",     # 1.5pt distance
            "dir": "5400000",    # direction: below
            "algn": "t",
        })
        srgbClr = outerShdw.makeelement(qn("a:srgbClr"), {"val": "000000"})
        alpha = srgbClr.makeelement(qn("a:alpha"), {"val": "60000"})  # 60% opacity
        srgbClr.append(alpha)
        outerShdw.append(srgbClr)
        effectLst.append(outerShdw)
        rPr.append(effectLst)
    except Exception:
        pass  # Shadow is cosmetic — don't fail the build


# ── Solid-color fallback mode (legacy) ──

def _set_white_background(slide) -> None:
    """Set the slide background to solid white."""
    background = slide.background
    fill = background.fill
    fill.solid()
    fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)


def _apply_zone_formatting(paragraph, zone: dict, *, default_size_pt: int = 20, default_color_hex: str = "333333") -> None:
    """Apply template formatting to a paragraph, falling back to defaults.

    Reads the zone's ``formatting`` dict (from template meta.json) and applies
    font_name, font_size_pt, font_color, alignment.  If any value is missing
    or null, uses the provided defaults.
    """
    fmt = zone.get("formatting", {}) or {}
    font = paragraph.font

    # Font size — template value first, then default
    size_pt = fmt.get("font_size_pt")
    if size_pt is not None and size_pt > 0:
        font.size = Pt(size_pt)
    else:
        font.size = Pt(default_size_pt)

    # Font name — only set if template provides a non-null value
    font_name = fmt.get("font_name")
    if font_name:
        font.name = font_name

    # Font color — template value first, then default
    font_color = fmt.get("font_color")
    if font_color:
        try:
            font.color.rgb = RGBColor.from_string(font_color)
        except Exception:
            font.color.rgb = RGBColor.from_string(default_color_hex)
    else:
        font.color.rgb = RGBColor.from_string(default_color_hex)

    # Alignment
    alignment = fmt.get("alignment")
    if alignment:
        _ALIGN_MAP = {"CENTER": PP_ALIGN.CENTER, "LEFT": PP_ALIGN.LEFT, "RIGHT": PP_ALIGN.RIGHT, "JUSTIFY": PP_ALIGN.JUSTIFY}
        paragraph.alignment = _ALIGN_MAP.get(str(alignment).upper(), PP_ALIGN.LEFT)


def _add_title_zone(slide, zone: dict) -> None:
    """Add a title text box (solid background mode). Uses template formatting when available."""
    text = str(zone.get("content") or "")
    left, top, width, height = _emu_rect(zone["position"])
    txbox = slide.shapes.add_textbox(Emu(left), Emu(top), Emu(width), Emu(height))
    tf = txbox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = text
    p.alignment = PP_ALIGN.LEFT
    _apply_zone_formatting(p, zone, default_size_pt=title_font_size(text), default_color_hex="333333")
    p.font.bold = True


def _add_bullets_zone(slide, zone: dict) -> None:
    """Add a bullet-list text box (solid background mode)."""
    content = zone.get("content") or []
    items: list[str] = content if isinstance(content, list) else [str(content)]
    left, top, width, height = _emu_rect(zone["position"])
    txbox = slide.shapes.add_textbox(Emu(left), Emu(top), Emu(width), Emu(height))
    tf = txbox.text_frame
    tf.word_wrap = True

    font_sz = bullet_font_size(items)

    for idx, item in enumerate(items):
        if idx == 0:
            p = tf.paragraphs[0]
        else:
            p = tf.add_paragraph()
        p.text = item
        p.alignment = PP_ALIGN.LEFT
        p.level = 0
        pPr = p._pPr
        if pPr is None:
            pPr = p._p.get_or_add_pPr()
        buChar = pPr.makeelement(qn("a:buChar"), {"char": "\u2022"})
        for old in pPr.findall(qn("a:buNone")):
            pPr.remove(old)
        for old in pPr.findall(qn("a:buChar")):
            pPr.remove(old)
        pPr.append(buChar)

        font = p.font
        font.size = Pt(font_sz)
        # Use template font_name/color if available
        _apply_zone_formatting(p, zone, default_size_pt=font_sz, default_color_hex="444444")
        # Restore size — _apply_zone_formatting may have overridden it
        if not (zone.get("formatting", {}) or {}).get("font_size_pt"):
            font.size = Pt(font_sz)


def _add_image_zone(slide, zone: dict, workspace_root: Path | None) -> None:
    """Add an image or a placeholder rectangle (solid background mode)."""
    left, top, width, height = _emu_rect(zone["position"])
    image_path_str = zone.get("image_path") or zone.get("image_ref")
    resolved = _resolve_image_path(image_path_str, workspace_root) if image_path_str else None

    if resolved is not None:
        slide.shapes.add_picture(str(resolved), Emu(left), Emu(top), Emu(width), Emu(height))
    else:
        shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Emu(left), Emu(top), Emu(width), Emu(height))
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor(0xEE, 0xF2, 0xF6)
        shape.line.color.rgb = RGBColor(0xB8, 0xC2, 0xCC)
        shape.line.width = Pt(1)
        tf = shape.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        label = "Image placeholder"
        if image_path_str:
            label = f"Image: {image_path_str} (not found)"
        p.text = label
        p.alignment = PP_ALIGN.CENTER
        p.font.size = Pt(14)
        p.font.color.rgb = RGBColor(0x99, 0x99, 0x99)


def _add_chart_zone(slide, zone: dict) -> None:
    """Add a chart (solid background mode)."""
    left, top, width, height = _emu_rect(zone["position"])
    chart_data_raw = zone.get("chart_data")

    if chart_data_raw and isinstance(chart_data_raw, dict):
        try:
            from pptx.chart.data import CategoryChartData
            chart_data = CategoryChartData()
            categories = chart_data_raw.get("categories", [])
            chart_data.categories = categories
            for series in chart_data_raw.get("series", []):
                chart_data.add_series(
                    series.get("name", "Series"),
                    series.get("values", [0] * len(categories)),
                )
            chart_type_str = chart_data_raw.get("chart_type", "bar")
            chart_type_map = {
                "bar": XL_CHART_TYPE.COLUMN_CLUSTERED,
                "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
                "line": XL_CHART_TYPE.LINE,
                "pie": XL_CHART_TYPE.PIE,
                "area": XL_CHART_TYPE.AREA,
            }
            xl_type = chart_type_map.get(chart_type_str, XL_CHART_TYPE.COLUMN_CLUSTERED)
            slide.shapes.add_chart(xl_type, Emu(left), Emu(top), Emu(width), Emu(height), chart_data)
            return
        except Exception:
            logger.warning("Failed to create chart from chart_data; falling back to placeholder", exc_info=True)

    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Emu(left), Emu(top), Emu(width), Emu(height))
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor(0xE8, 0xED, 0xF2)
    shape.line.color.rgb = RGBColor(0xA0, 0xAE, 0xBE)
    shape.line.width = Pt(1)
    tf = shape.text_frame
    tf.word_wrap = True
    tf.paragraphs[0].text = "Chart placeholder"
    tf.paragraphs[0].alignment = PP_ALIGN.CENTER
    tf.paragraphs[0].font.size = Pt(16)
    tf.paragraphs[0].font.color.rgb = RGBColor(0x66, 0x66, 0x66)


def _add_shape_zone(slide, zone: dict) -> None:
    """Add a generic shape (solid background mode)."""
    left, top, width, height = _emu_rect(zone["position"])

    shape_type_str = zone.get("shape_type", "roundRect")
    shape_type_map = {
        "roundRect": MSO_SHAPE.ROUNDED_RECTANGLE,
        "rect": MSO_SHAPE.RECTANGLE,
        "ellipse": MSO_SHAPE.OVAL,
        "oval": MSO_SHAPE.OVAL,
        "diamond": MSO_SHAPE.DIAMOND,
        "triangle": MSO_SHAPE.ISOSCELES_TRIANGLE,
    }
    mso_shape = shape_type_map.get(shape_type_str, MSO_SHAPE.ROUNDED_RECTANGLE)

    shape = slide.shapes.add_shape(mso_shape, Emu(left), Emu(top), Emu(width), Emu(height))

    fill_color = zone.get("fill_color")
    if fill_color:
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor.from_string(fill_color)
    else:
        shape.fill.background()

    line_color = zone.get("line_color")
    if line_color:
        shape.line.color.rgb = RGBColor.from_string(line_color)
        shape.line.width = Pt(1)
    else:
        shape.line.fill.background()

    text = zone.get("content")
    if text:
        tf = shape.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = str(text)
        p.alignment = PP_ALIGN.CENTER
        p.font.size = Pt(14)
        p.font.color.rgb = RGBColor(0x33, 0x33, 0x33)


# ── Slide builders ──

def _build_slide_with_background(
    prs: Presentation,
    slide_data: dict,
    slide_no: int,
    workspace_root: Path | None,
) -> None:
    """Build a slide with a full-page background image + text overlay.

    This is the NEW mode: background image fills the page, text zones
    are transparent overlays.
    """
    blank_layout = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank_layout)

    # 1. Set background image
    bg_path = _resolve_background_image(slide_data, workspace_root)
    if bg_path:
        _set_background_image(slide, bg_path)
    else:
        # No background image — use solid color
        _set_white_background(slide)

    # 2. Overlay text zones
    for zone in slide_data.get("zones", []):
        zone_type = zone.get("type", "")
        if zone_type in ("title", "subtitle", "bullets", "body", "footer"):
            if bg_path:
                # Background image mode: white text with shadow
                _add_text_overlay(slide, zone)
            else:
                # Solid background mode: dark text, legacy style
                if zone_type == "title":
                    _add_title_zone(slide, zone)
                elif zone_type in ("bullets", "body"):
                    _add_bullets_zone(slide, zone)
        elif zone_type == "image":
            # Add separate images in ALL modes — independently editable layer
            # on top of the background image (or solid-color fallback)
            # ``preserve`` only has meaning when a source template shape
            # exists.  In blank fallback mode it must not manufacture a gray
            # placeholder that was never requested.
            if zone.get("action") != "preserve":
                _add_image_zone(slide, zone, workspace_root)
        elif zone_type == "chart":
            _add_chart_zone(slide, zone)
        elif zone_type == "shape":
            _add_shape_zone(slide, zone)

    # Fallback title if none exists
    has_title = any(z.get("type") == "title" for z in slide_data.get("zones", []))
    if not has_title:
        txbox = slide.shapes.add_textbox(
            Emu(int(0.08 * SLIDE_W)),
            Emu(int(0.08 * SLIDE_H)),
            Emu(int(0.84 * SLIDE_W)),
            Emu(int(0.14 * SLIDE_H)),
        )
        tf = txbox.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = f"Slide {slide_no}"
        p.font.size = Pt(30)
        p.font.bold = True
        if bg_path:
            p.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        else:
            p.font.color.rgb = RGBColor(0x33, 0x33, 0x33)


def _resolve_background_image(
    slide_data: dict,
    workspace_root: Path | None,
) -> Path | None:
    """Find the background image for a slide.

    Checks (in order):
    1. slide_data["background_image"] — explicit background path
    2. background_images/slide_XX.png — generated by img2img
    3. generated_slides/slide_XX.png — legacy location
    """
    # Explicit background
    bg = slide_data.get("background_image")
    if bg:
        resolved = _resolve_image_path(bg, workspace_root)
        if resolved:
            return resolved

    # Auto-detect from workspace directories
    if workspace_root:
        idx = slide_data.get("slide_index", 0)
        for subdir in ("background_images", "generated_slides"):
            for pattern in [f"slide-{idx:03d}.png", f"slide_{idx:02d}.png", f"slide_{idx}.png"]:
                candidate = workspace_root / subdir / pattern
                if candidate.exists():
                    return candidate

    return None


# ── Legacy slide builder (solid background) ──

def _build_slide_legacy(
    prs: Presentation,
    slide_data: dict,
    slide_no: int,
    workspace_root: Path | None,
) -> None:
    """Create a single slide from zone descriptions (legacy solid-bg mode)."""
    blank_layout = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank_layout)
    _set_white_background(slide)

    has_title = False
    for zone in slide_data.get("zones", []):
        zone_type = zone.get("type", "")
        if zone_type == "title":
            _add_title_zone(slide, zone)
            has_title = True
        elif zone_type in ("bullets", "body"):
            _add_bullets_zone(slide, zone)
        elif zone_type == "image":
            if zone.get("action") != "preserve":
                _add_image_zone(slide, zone, workspace_root)
        elif zone_type == "chart":
            _add_chart_zone(slide, zone)
        elif zone_type == "shape":
            _add_shape_zone(slide, zone)
        else:
            logger.warning("Unknown zone type '%s' on slide %d; skipping", zone_type, slide_no)

    if not has_title:
        txbox = slide.shapes.add_textbox(
            Emu(int(0.08 * SLIDE_W)),
            Emu(int(0.08 * SLIDE_H)),
            Emu(int(0.84 * SLIDE_W)),
            Emu(int(0.14 * SLIDE_H)),
        )
        tf = txbox.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = f"Slide {slide_no}"
        p.font.size = Pt(30)
        p.font.bold = True
        p.font.color.rgb = RGBColor(0x33, 0x33, 0x33)


# ── Public API ──

def write_pptx(
    slide_contents: dict,
    output_path: Path | str,
    workspace_root: Path | None = None,
    template_path: Path | None = None,
) -> Path:
    """Write a PPTX file from *slide_contents*.

    When *template_path* is provided, the template's slides are KEPT as
    visual backgrounds — text zones from the outline are overlaid on top
    of the existing template slides.  If the outline has more slides than
    the template, template slides are cycled.

    Without a template, blank 16:9 slides are created with AI-generated
    backgrounds or solid-color fallbacks.
    """
    slides = slide_contents.get("slides", [])
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if template_path and Path(template_path).exists():
        prs = Presentation(str(template_path))
        # Template mode: keep template slides, overlay text
        return _write_with_template(prs, slides, output_path, workspace_root)
    else:
        prs = Presentation()
        prs.slide_width = Emu(SLIDE_W)
        prs.slide_height = Emu(SLIDE_H)
        for index, slide_data in enumerate(slides, start=1):
            _build_slide_with_background(prs, slide_data, index, workspace_root)
        prs.save(str(output_path))
        return output_path


def _write_with_template(
    prs: Presentation,
    slides: list[dict],
    output_path: Path,
    workspace_root: Path | None,
) -> Path:
    """Build PPT using template slides as visual base, overlaying text.

    Strategy:
    1. Keep all template slides as-is (preserve visual design).
    2. For each outline slide, clone a matching template slide.
    3. Overlay text zones from the outline on the cloned slide.
    4. If outline has more slides than template, cycle: use template[idx % N].
    5. Delete unused template slides at the end.
    """
    n_template = len(prs.slides)
    n_outline = len(slides)
    logger.info("Template-based assembly: %d template slides, %d outline slides",
                n_template, n_outline)

    # Collect template slide IDs before cloning (to delete later)
    original_slide_ids = [slide.slide_id for slide in prs.slides]

    # For each outline slide, clone a template slide and overlay text
    for idx, slide_data in enumerate(slides):
        tpl_idx = idx % n_template
        template_slide = prs.slides[tpl_idx]

        # Clone the template slide
        _clone_slide(prs, template_slide)

        # The cloned slide is now the LAST slide
        new_slide = prs.slides[-1]

        # Overlay text zones on the cloned slide
        _overlay_text_on_slide(new_slide, slide_data, workspace_root)

        # Replace images in image zones with generated/user images
        _replace_images_on_slide(new_slide, slide_data, workspace_root)

    # Delete original template slides (keep only cloned + overlaid ones), newest first
    for del_idx in range(len(original_slide_ids) - 1, -1, -1):
        _delete_slide_by_index(prs, del_idx)

    prs.save(str(output_path))
    return output_path


# ── Agent-driven assembly (zone_id precise matching) ──

def _inject_zone_ids(slide, tpl_slide: dict) -> dict[str, object]:
    """Match template zones to PPTX shapes by position overlap.

    For each template zone, finds the shape whose position has the largest
    **positive overlap** that is also reasonably close in size.  Each shape
    is assigned to at most one zone, and each zone gets at most one shape.

    This prevents a single large "background" zone from capturing every
    shape on the slide (common in some template_zones analyses).

    Returns:
        ``{zone_id: shape}`` — at most one shape per zone_id.
    """
    all_zones = tpl_slide.get("all_zones", [])
    if not all_zones:
        return {}

    # Resolve stable XML paths first, including descendants of group shapes.
    path_map: dict[str, object] = {}

    def collect_paths(shapes, prefix: str) -> None:
        for ordinal, shape in enumerate(shapes):
            native_id = getattr(shape, "shape_id", ordinal)
            if hasattr(shape, "shapes"):
                collect_paths(shape.shapes, f"{prefix}/group-{native_id}")
            else:
                path_map[f"{prefix}/shape-{native_id}"] = shape

    collect_paths(slide.shapes, f"slide-{tpl_slide.get('index', 0) + 1}")

    # Collect all leaf shapes for legacy geometric fallback.
    shape_candidates: list[tuple[float, float, float, float, object]] = []

    def collect_candidates(shapes) -> None:
        for shape in shapes:
            if hasattr(shape, "shapes"):
                collect_candidates(shape.shapes)
                continue
            try:
                sx = (shape.left or 0) / SLIDE_W
                sy = (shape.top or 0) / SLIDE_H
                sw = (shape.width or 0) / SLIDE_W
                sh = (shape.height or 0) / SLIDE_H
                shape_candidates.append((sx, sy, sw, sh, shape))
            except Exception:
                continue

    collect_candidates(slide.shapes)

    # For each zone, find the BEST matching shape (then remove it from pool)
    zone_id_map: dict[str, object] = {}
    assigned_shape_indices: set[int] = set()

    for zone in all_zones:
        pos = zone.get("position", [0, 0, 0, 0])
        if len(pos) < 4:
            continue
        zid = zone.get("zone_id", "")
        if not zid:
            continue
        shape_path = zone.get("shape_path") or zid
        path_shape = path_map.get(shape_path)
        if path_shape is not None:
            direct_idx = next(
                (i for i, candidate in enumerate(shape_candidates)
                 if i not in assigned_shape_indices and candidate[4] is path_shape),
                None,
            )
            if direct_idx is not None:
                zone_id_map[zid] = path_shape
                assigned_shape_indices.add(direct_idx)
                continue
        native_shape_id = zone.get("native_shape_id")
        if native_shape_id is not None:
            direct_idx = next(
                (
                    i for i, (_sx, _sy, _sw, _sh, candidate) in enumerate(shape_candidates)
                    if i not in assigned_shape_indices
                    and getattr(candidate, "shape_id", None) == native_shape_id
                ),
                None,
            )
            if direct_idx is not None:
                _sx, _sy, _sw, _sh, shape = shape_candidates[direct_idx]
                zone_id_map[zid] = shape
                assigned_shape_indices.add(direct_idx)
                continue
        zx, zy, zw, zh = pos[0], pos[1], pos[2], pos[3]
        zone_area = zw * zh

        best_idx = -1
        best_score = -1.0

        for i, (sx, sy, sw, sh, _shape) in enumerate(shape_candidates):
            if i in assigned_shape_indices:
                continue

            # Intersection area
            ox = max(0.0, min(sx + sw, zx + zw) - max(sx, zx))
            oy = max(0.0, min(sy + sh, zy + zh) - max(sy, zy))
            overlap = ox * oy

            if overlap <= 0.0:
                continue

            shape_area = sw * sh

            # Score = how much of the shape is inside the zone
            # (overlap / shape_area).  Prefer shapes that fit snugly.
            # Penalise when the zone is much larger than the shape
            # (giant background zones should not steal small text shapes).
            if shape_area > 0:
                fill_ratio = overlap / min(shape_area, zone_area)
            else:
                fill_ratio = 0.0

            # Boost for shapes that have similar area to the zone
            if zone_area > 0 and shape_area > 0:
                size_ratio = min(shape_area, zone_area) / max(shape_area, zone_area)
                size_bonus = size_ratio * 0.5
            else:
                size_bonus = 0.0

            score = fill_ratio + size_bonus

            if score > best_score:
                best_score = score
                best_idx = i

        # Require a meaningful match: fill ratio alone ≥ 1%
        if best_idx >= 0 and best_score >= 0.01:
            _sx, _sy, _sw, _sh, shape = shape_candidates[best_idx]
            if shape.name and not shape.name.startswith("_"):
                shape._original_name = shape.name
            shape.name = zid
            zone_id_map[zid] = shape
            assigned_shape_indices.add(best_idx)
            logger.debug(
                "zone_id=%s → shape '%s' (score=%.3f, zone_type=%s)",
                zid,
                getattr(shape, "_original_name", shape.name),
                best_score,
                zone.get("type", "?"),
            )

    return zone_id_map


def write_pptx_from_mapping(
    template_path: Path,
    template_zones: dict,
    mappings: dict[int, dict],
    image_mappings: dict[int, dict],
    slide_contents: dict,
    output_path: Path | str,
    workspace_root: Path | None = None,
    outline: dict | None = None,
) -> Path:
    """Assemble PPTX using zone_id → content mappings.

    Copies the template PPTX and modifies slides **in place** — no cloning,
    no deleting.  This avoids ``_delete_slides_by_id`` corruption and
    ``deepcopy`` / lxml compatibility issues with complex templates.

    Guardrails:
    - Every zone_id is validated against the actual template shapes.
    - Invalid zone_ids are logged and skipped.
    - Slides with no mappings fall back to ``_overlay_text_on_slide``.
    """
    import shutil

    slides = slide_contents.get("slides", [])
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Copy the template so we can mutate it safely
    shutil.copy2(str(template_path), str(output_path))
    prs = Presentation(str(output_path))
    n_template = len(prs.slides)

    tpl_slides_by_index = {s["index"]: s for s in template_zones.get("slides", [])}
    assembly_mode = str(
        slide_contents.get("assembly_policy", {}).get("mode")
        or "text_replace_only"
    )
    outline_by_index = {
        int(item.get("slide_index", index)): item
        for index, item in enumerate((outline or {}).get("slides", []))
    }

    invalid_zone_ids: list[str] = []
    skipped_slides: list[int] = []
    selected_template_indices: list[int] = []

    for idx, slide_data in enumerate(slides):
        tpl_idx = int(slide_data.get("template_slide_index", idx % n_template))
        if tpl_idx >= len(prs.slides):
            logger.warning("Slide %d: template only has %d slides — skipping", idx, n_template)
            continue

        selected_template_indices.append(tpl_idx)
        slide = prs.slides[tpl_idx]
        tpl_zone_data = tpl_slides_by_index.get(tpl_idx, {})

        # A generated full-page visual is a complete replacement visual layer,
        # not an inline image. Build the editable text layer over it. Previous
        # versions generated these images but the strict template path never
        # consumed them, so every run silently kept unrelated template photos.
        full_page_background = (
            _resolve_background_image(slide_data, workspace_root)
            if assembly_mode == "generated_background_replace"
            else None
        )
        if full_page_background is not None:
            for shape in list(slide.shapes):
                slide.shapes._spTree.remove(shape._element)
            _set_background_image(slide, full_page_background)

            slide_index = int(slide_data.get("slide_index", idx))
            if slide_index not in (0, 11):
                raw_zones = _outline_background_text_zones(
                    slide_data,
                    outline_by_index.get(slide_index),
                )
            else:
                raw_zones = []
            for zone in slide_data.get("zones", []):
                if slide_index not in (0, 11):
                    break
                formatting = zone.get("formatting", {}) or {}
                position = zone.get("position", [0, 0, 0, 0])
                content_text = str(zone.get("content") or "").strip()
                is_oversized_decorative_glyph = (
                    zone.get("type") == "title"
                    and len(content_text) <= 1
                    and float(formatting.get("font_size_pt") or 0) >= 72
                )
                if (
                    not is_oversized_decorative_glyph
                    and
                    zone.get("action") == "replace_text"
                    and zone.get("type")
                    in ("title", "subtitle", "bullets", "body")
                    and zone.get("content") not in (None, "", [])
                    and len(position) >= 4
                    and float(position[2]) >= 0.04
                    and len(content_text) > 1
                ):
                    raw_zones.append(zone)

            # Closing slides should be deliberately sparse: one section label,
            # one audience-facing closing sentence, and one forward-looking
            # line. Template card microcopy otherwise creates a wall of text.
            if slide_index == 11:
                header = [
                    z for z in raw_zones
                    if z.get("type") == "title"
                    and float(z.get("position", [0, 1])[1]) < 0.15
                ]
                body = [z for z in raw_zones if z.get("type") == "body"]
                closing = [
                    z for z in raw_zones
                    if z.get("type") == "title"
                    and float(z.get("position", [0, 0])[1]) >= 0.15
                ]
                raw_zones = (
                    header[:1]
                    + sorted(
                        body,
                        key=lambda z: len(str(z.get("content") or "")),
                        reverse=True,
                    )[:1]
                    + sorted(
                        closing,
                        key=lambda z: len(str(z.get("content") or "")),
                        reverse=True,
                    )[:1]
                )

            selected_zones: list[dict] = []
            seen_copy: set[str] = set()
            for zone in raw_zones:
                content_key = " ".join(
                    str(zone.get("content") or "").split()
                ).lower()
                if content_key in seen_copy:
                    continue
                position = zone.get("position", [0, 0, 0, 0])
                area = max(float(position[2]) * float(position[3]), 1e-9)
                overlap_index = None
                for existing_index, existing in enumerate(selected_zones):
                    existing_pos = existing.get("position", [0, 0, 0, 0])
                    left = max(float(position[0]), float(existing_pos[0]))
                    top = max(float(position[1]), float(existing_pos[1]))
                    right = min(
                        float(position[0]) + float(position[2]),
                        float(existing_pos[0]) + float(existing_pos[2]),
                    )
                    bottom = min(
                        float(position[1]) + float(position[3]),
                        float(existing_pos[1]) + float(existing_pos[3]),
                    )
                    intersection = max(0.0, right - left) * max(0.0, bottom - top)
                    existing_area = max(
                        float(existing_pos[2]) * float(existing_pos[3]), 1e-9
                    )
                    if intersection / min(area, existing_area) >= 0.65:
                        overlap_index = existing_index
                        break
                if overlap_index is not None:
                    existing = selected_zones[overlap_index]
                    if len(content_key) > len(
                        " ".join(str(existing.get("content") or "").split())
                    ):
                        selected_zones[overlap_index] = zone
                    continue
                selected_zones.append(zone)
                seen_copy.add(content_key)

            for zone in selected_zones:
                if (
                    slide_index == 0
                    and zone.get("type") == "title"
                    and float(zone.get("position", [0, 1])[1]) < 0.15
                ):
                    zone = dict(zone)
                    zone["formatting"] = dict(zone.get("formatting", {}) or {})
                    zone["formatting"]["font_size_pt"] = max(
                        36, float(zone["formatting"].get("font_size_pt") or 0)
                    )
                _add_text_overlay(slide, zone)
            continue

        # Inject zone_ids into shapes by position overlap
        zone_id_map = _inject_zone_ids(slide, tpl_zone_data)
        strict_text_replace = (
            slide_contents.get("assembly_policy", {}).get("mode", "text_replace_only")
            == "text_replace_only"
        )

        if not zone_id_map:
            logger.warning(
                "Slide %d: _inject_zone_ids returned empty map — "
                "falling back to heuristic overlay",
                idx,
            )
            if not strict_text_replace:
                _overlay_text_on_slide(slide, slide_data, workspace_root)
                _replace_images_on_slide(slide, slide_data, workspace_root)
            continue

        slide_mappings = mappings.get(idx, {})
        img_mappings = image_mappings.get(idx, {})

        if not slide_mappings and not img_mappings:
            skipped_slides.append(idx)
            if not strict_text_replace:
                _overlay_text_on_slide(slide, slide_data, workspace_root)
                _replace_images_on_slide(slide, slide_data, workspace_root)
            continue

        matched_zone_ids: set[str] = set()

        # ── Text replacement: zone_id → precise shape ──
        for mapping_key, entry in slide_mappings.items():
            if not isinstance(entry, dict):
                continue

            zid = entry.get("zone_id", "")
            content_type = entry.get("type", mapping_key)
            action = entry.get("action", "replace_text")
            content = entry.get("content")
            if not zid:
                continue
            if action == "preserve":
                matched_zone_ids.add(zid)
                continue

            # _overlay_ prefixed zone_ids mean "add a new text box" —
            # the template has no matching shape for this content type.
            if zid.startswith("_overlay_"):
                if strict_text_replace:
                    invalid_zone_ids.append(zid)
                    logger.warning("Slide %d: overlay zone '%s' rejected by strict policy", idx, zid)
                elif content is not None:
                    # Determine position from slide_data zones
                    slide_zones = slide_data.get("zones", [])
                    overlay_zone = _find_zone_by_id(slide_zones, zid)
                    if overlay_zone:
                        _add_text_overlay(slide, overlay_zone)
                    else:
                        # Fallback: use a sensible position
                        if content_type == "title":
                            _add_text_overlay(slide, {
                                "type": "title", "content": content,
                                "position": [0.08, 0.08, 0.84, 0.16],
                            })
                        elif content_type in ("bullets", "body"):
                            _add_text_overlay(slide, {
                                "type": "bullets", "content": content,
                                "position": [0.10, 0.28, 0.56, 0.54],
                            })
                matched_zone_ids.add(zid)
                continue

            shape = zone_id_map.get(zid)
            if shape is None:
                if strict_text_replace:
                    invalid_zone_ids.append(zid)
                    logger.warning(
                        "Slide %d: zone_id='%s' does not exist on the template slide "
                        "(valid: %s) — skipping",
                        idx, zid, sorted(zone_id_map.keys())[:10],
                    )
                else:
                    slide_zones = slide_data.get("zones", [])
                    overlay_zone = _find_zone_by_id(slide_zones, zid)
                    if overlay_zone:
                        _add_text_overlay(slide, overlay_zone)
                        matched_zone_ids.add(zid)
                continue

            if action == "clear_text":
                _clear_shape_text(shape)
            elif content is not None:
                # If the matched shape is a decoration / image / non-text
                # element (no text_frame), create an overlay text box at
                # the zone's position instead of silently failing.
                if not (hasattr(shape, "has_text_frame") and shape.has_text_frame):
                    if strict_text_replace:
                        invalid_zone_ids.append(zid)
                        logger.warning("Slide %d: zone '%s' is not an editable text shape", idx, zid)
                        continue
                    slide_zones = slide_data.get("zones", [])
                    overlay_zone = _find_zone_by_id(slide_zones, zid)
                    if overlay_zone:
                        _add_text_overlay(slide, overlay_zone)
                    else:
                        _add_text_overlay(slide, {
                            "type": content_type, "content": content,
                            "position": [0.08, 0.08, 0.84, 0.16] if content_type == "title"
                            else [0.10, 0.28, 0.56, 0.54],
                        })
                else:
                    _apply_text_to_shape(
                        shape,
                        {"type": content_type, "content": content},
                    )
            matched_zone_ids.add(zid)

        # ── Image replacement ──
        for zid, img_path_str in img_mappings.items():
            shape = zone_id_map.get(zid)
            if shape is None:
                invalid_zone_ids.append(zid)
                continue

            img_path = Path(img_path_str)
            if not img_path.is_absolute() and workspace_root:
                img_path = workspace_root / img_path_str
            if img_path.exists():
                _replace_shape_image(shape, img_path)
                matched_zone_ids.add(zid)

        # ── Clear unmatched text shapes ──
        # 1) Shapes that matched a zone but got no content → clear.
        if not strict_text_replace:
            for zid, shape in zone_id_map.items():
                if zid not in matched_zone_ids:
                    _clear_shape_text(shape)

        # 2) Shapes that didn't match ANY zone (page numbers,
        #    designer credits, decorative text) → clear to prevent
        #    old template placeholder text from leaking into output.
        #    Compare by .name (zone_id) — NOT by id(), because
        #    python-pptx creates new wrapper objects on each iteration.
        if not strict_text_replace:
            matched_names = {s.name for s in zone_id_map.values()}
            for shape in slide.shapes:
                if shape.name not in matched_names:
                    _clear_shape_text(shape)

    # ── Remove unused template slides (when outline < template) ──
    if len(set(selected_template_indices)) == len(selected_template_indices):
        keep = set(selected_template_indices)
        for del_idx in range(n_template - 1, -1, -1):
            if del_idx not in keep:
                _delete_slide_by_index(prs, del_idx)
    else:
        logger.warning(
            "Template slide selection contains duplicates; source pages cannot "
            "be reused safely in strict in-place assembly"
        )

    # ── Summary ──
    if invalid_zone_ids:
        logger.warning(
            "Agent provided %d invalid zone_ids total: %s",
            len(invalid_zone_ids),
            invalid_zone_ids[:20],
        )
    if skipped_slides:
        logger.info(
            "%d slides had no agent mappings — used heuristic fallback: %s",
            len(skipped_slides), skipped_slides,
        )

    prs.save(str(output_path))
    logger.info("Mapping-based assembly complete: %s", output_path)
    return output_path


def _clone_shapes_from_source(target_slide, source_slide) -> None:
    """Clone all shapes from *source_slide* onto *target_slide*.

    Copies the full XML of each shape (including formatting, images, and
    relationships) via ``lxml.deepcopy``.  Does NOT clone the slide itself
    — only its shapes.

    Used by ``write_pptx_from_mapping`` to build a clean presentation
    without the ``_delete_slides_by_id`` corruption risk.
    """
    from copy import deepcopy as _deepcopy

    # Remove any default shapes the blank layout may have placed
    for shape in list(target_slide.shapes):
        sp = shape._element
        sp.getparent().remove(sp)

    # Deep-copy every shape from the source slide
    for shape in source_slide.shapes:
        el = _deepcopy(shape._element)
        target_slide.shapes._spTree.append(el)


def _clone_slide(prs: Presentation, source_slide) -> None:
    """Clone a slide by copying its XML and adding it to the presentation."""
    from lxml import etree
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT

    # Get the slide layout used by the source
    slide_layout = source_slide.slide_layout

    # Add a new slide with the same layout
    new_slide = prs.slides.add_slide(slide_layout)

    # Copy all shapes from source to new slide
    # Remove default shapes on the new slide
    for shape in list(new_slide.shapes):
        sp = shape._element
        sp.getparent().remove(sp)

    # Copy shapes from source
    for shape in source_slide.shapes:
        el = _copy_element(shape._element)
        new_slide.shapes._spTree.append(el)

    # Copy slide background
    _copy_slide_background(source_slide, new_slide)


def _copy_element(el):
    """Deep-copy an XML element."""
    from copy import deepcopy
    return deepcopy(el)


def _copy_slide_background(source, target) -> None:
    """Copy background from source slide to target slide."""
    from lxml import etree
    ns = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main"}
    src_bg = source._element.find(".//p:cSld/p:bg", ns)
    if src_bg is not None:
        tgt_csld = target._element.find(".//p:cSld", ns)
        if tgt_csld is not None:
            # Remove existing bg if any
            existing = tgt_csld.find("p:bg", ns)
            if existing is not None:
                tgt_csld.remove(existing)
            tgt_csld.insert(0, deepcopy(src_bg))


def _delete_slide_by_index(prs: Presentation, index: int) -> None:
    """Delete a single slide by its index in the presentation.

    Uses python-pptx's internal API — safer than deleting by slide_id
    across the entire list, because we delete from the end working
    backwards so indices never shift.
    """
    rId = prs.slides._sldIdLst[index].get(
        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    )
    if rId is not None:
        prs.part.drop_rel(rId)
    prs.slides._sldIdLst.remove(prs.slides._sldIdLst[index])


def _delete_slides_by_id(prs: Presentation, slide_ids: list[int]) -> None:
    """Delete slides by their slide_id."""
    NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    sld_id_lst = prs.slides._sldIdLst
    for slide_id in slide_ids:
        for elem in sld_id_lst:
            if elem.get("id") == str(slide_id):
                rId = elem.get(f"{{{NS}}}id")
                if rId is None:
                    # Try unprefixed attribute (some PPTX variants)
                    rId = elem.get("id")
                if rId is not None:
                    prs.part.drop_rel(rId)
                sld_id_lst.remove(elem)
                break


def _overlay_text_on_slide(slide, slide_data: dict, workspace_root: Path | None) -> None:
    """Replace text in template shapes with user content.

    Strategy (type-priority + reading-order matching, NO position dependency):
    1. Sort template text shapes by placeholder type priority, then reading order.
    2. Sort content zones by type priority (title > subtitle > bullets > body).
    3. Match by index within each type group — one zone to one shape.
    4. Replace text in matched shapes (preserving template formatting).
    5. Clear ALL unmatched shapes (remove old template text).
    6. NEVER add new text boxes (user said: use template positions only).
    """
    zones = slide_data.get("zones", [])
    content_zones = [
        z for z in zones
        if z.get("content") and z.get("type") in ("title", "subtitle", "bullets", "body")
    ]
    if not content_zones:
        # No content to place — still clear template placeholder text
        for shape in slide.shapes:
            if shape.has_text_frame:
                _clear_shape_text(shape)
        return

    # Collect text shapes and classify by visual role (font-based)
    text_shapes = [s for s in slide.shapes if s.has_text_frame]

    # No text shapes on this slide (e.g. cover with only images/decoration)
    # → add new text boxes directly instead of trying to match template shapes.
    if not text_shapes:
        for zone in content_zones:
            ztype = zone.get("type", "")
            if ztype == "title":
                _add_title_zone(slide, zone)
            elif ztype in ("bullets", "body"):
                _add_bullets_zone(slide, zone)
            elif ztype == "subtitle":
                _add_text_overlay(slide, zone)
        return

    # Group shapes by inferred type role
    shape_groups: dict[str, list[int]] = {"title": [], "subtitle": [], "bullets": [], "body": [], "other": []}
    for si, shape in enumerate(text_shapes):
        role = _infer_shape_role(shape)
        shape_groups[role].append(si)

    # Group content zones by type
    zone_groups: dict[str, list[int]] = {"title": [], "subtitle": [], "bullets": [], "body": []}
    for zi, zone in enumerate(content_zones):
        ztype = zone.get("type", "body")
        if ztype not in zone_groups:
            ztype = "body"
        zone_groups[ztype].append(zi)

    matched_shape_ids: set[int] = set()

    # Match within each type group: zones → shapes (one-to-one, by reading order)
    # Sort helper: shapes higher on slide first, then wider ones
    def _sort_key(si):
        s = text_shapes[si]
        top = s.top if s.top else 0
        width = s.width if s.width else 0
        # Prefer shapes that are wider (title shapes are typically wide)
        return (top, -width)

    for ztype in ("title", "subtitle", "bullets", "body"):
        zlist = zone_groups.get(ztype, [])
        slist = sorted(shape_groups.get(ztype, []), key=_sort_key)

        for i, zi in enumerate(zlist):
            if i < len(slist):
                _apply_text_to_shape(text_shapes[slist[i]], content_zones[zi])
                matched_shape_ids.add(slist[i])
            else:
                # Fall back: pick the best available unmatched shape.
                # Prefer wider shapes in the upper portion, avoid narrow footers.
                candidates = [
                    si for si in range(len(text_shapes))
                    if si not in matched_shape_ids
                ]
                # Sort by: width DESC (prefer wide), then top ASC (prefer higher)
                candidates.sort(key=lambda si: (
                    0 if (text_shapes[si].width or 0) > 1000000 else 1,  # wide enough?
                    -(text_shapes[si].width or 0),  # wider = better
                    text_shapes[si].top if text_shapes[si].top else 0,  # higher = better
                ))
                if candidates:
                    _apply_text_to_shape(text_shapes[candidates[0]], content_zones[zi])
                    matched_shape_ids.add(candidates[0])

    # Clear ALL unmatched shapes
    for si, shape in enumerate(text_shapes):
        if si not in matched_shape_ids:
            _clear_shape_text(shape)


def _infer_shape_role(shape) -> str:
    """Infer the visual role of a shape based on position, size, and text.

    Returns one of: 'title', 'subtitle', 'bullets', 'body', 'other'.
    """
    # 1. Placeholder type from PPTX template (most reliable)
    try:
        ph = shape._element.find(
            ".//{http://schemas.openxmlformats.org/presentationml/2006/main}ph"
        )
        if ph is not None:
            ph_type = ph.get("type", "")
            if ph_type in ("title", "ctrTitle"):
                return "title"
            if ph_type == "subTitle":
                return "subtitle"
            if ph_type == "body":
                return "body"
    except Exception:
        pass

    if not shape.has_text_frame:
        return "other"

    txt = shape.text_frame.text.strip()
    if not txt:
        return "other"

    top = shape.top / SLIDE_H if shape.top else 0
    left = shape.left / SLIDE_W if shape.left else 0
    width = shape.width / SLIDE_W if shape.width else 0

    # ── Heuristics ──
    para_count = len(shape.text_frame.paragraphs)
    total_chars = sum(len(p.text or "") for p in shape.text_frame.paragraphs)

    # Title: near top (y<28%), width >15% (excludes narrow footers), short text (<80 chars)
    if top < 0.28 and width > 0.15 and total_chars < 80:
        return "title"

    # Subtitle: below title area (10%<y<45%), moderate width, moderate text
    if top < 0.45 and width > 0.15 and total_chars < 150 and para_count <= 2:
        return "subtitle"

    # Bullets: multiple paragraphs with short lines per paragraph
    if para_count >= 2 and total_chars > 20:
        return "bullets"

    # Body: substantial text or wide text area with content
    if total_chars >= 40 or (width > 0.15 and total_chars > 20):
        return "body"

    # Everything else: labels, page numbers, footers, template designer credits
    return "other"


def _apply_text_to_shape(shape, zone: dict) -> None:
    """Replace text in an existing template shape with new content.

    Preserves the shape's original font, color, and formatting.
    Only changes the text content.
    """
    if not hasattr(shape, "has_text_frame") or not shape.has_text_frame:
        return
    tf = shape.text_frame
    zone_type = zone.get("type", "body")
    content = zone.get("content", "")

    # Normalize content to list
    if isinstance(content, list):
        items = [str(c) for c in content]
    else:
        items = [str(content)]

    paragraphs = list(tf.paragraphs)
    if not paragraphs:
        return

    # Strict replacement does not add paragraphs, because doing so creates
    # new paragraph/run properties.  Extra logical items become line breaks
    # inside the last existing paragraph.
    if len(items) > len(paragraphs):
        keep = items[: len(paragraphs) - 1]
        keep.append("\n".join(items[len(paragraphs) - 1 :]))
        items = keep

    for index, paragraph in enumerate(paragraphs):
        value = items[index] if index < len(items) else ""
        _replace_paragraph_text_preserving_runs(paragraph, value)


def _replace_paragraph_text_preserving_runs(paragraph, text: str) -> None:
    """Change characters while retaining the paragraph's existing run XML."""
    if not str(text):
        # An empty paragraph can still render its inherited bullet glyph.
        # Explicitly disable bullets on cleared surplus paragraphs while
        # retaining the paragraph itself and all other template formatting.
        from pptx.oxml.xmlchemy import OxmlElement
        p_pr = paragraph._p.get_or_add_pPr()
        for child in list(p_pr):
            if child.tag.rsplit("}", 1)[-1] in {
                "buChar", "buAutoNum", "buBlip", "buNone"
            }:
                p_pr.remove(child)
        p_pr.append(OxmlElement("a:buNone"))
    runs = list(paragraph.runs)
    if runs:
        runs[0].text = str(text)
        for run in runs[1:]:
            run.text = ""
        return
    run = paragraph.add_run()
    run.text = str(text)


def _clear_shape_text(shape) -> None:
    """Clear all text from a shape (removes template placeholder text)."""
    if not hasattr(shape, "has_text_frame") or not shape.has_text_frame:
        return
    try:
        for p in shape.text_frame.paragraphs:
            _replace_paragraph_text_preserving_runs(p, "")
    except AttributeError:
        pass  # shape claims has_text_frame but has no text_frame (rare edge case)


def _find_zone_by_id(slide_zones: list[dict], zone_id: str) -> dict | None:
    """Return the zone dict with the given *zone_id*, or None."""
    for z in slide_zones:
        if z.get("zone_id") == zone_id:
            return z
    return None


def _replace_images_on_slide(slide, slide_data: dict, workspace_root: Path | None) -> None:
    """Replace template images with generated or user-provided images.

    For each image zone in slide_data that has an image_ref or generated
    output, find the visually closest image shape on the slide and replace
    its picture data.
    """
    image_zones = [
        z for z in slide_data.get("zones", [])
        if z.get("type") == "image"
    ]

    # Collect image shapes on the slide
    image_shapes = []
    for shape in slide.shapes:
        try:
            if shape.image:
                image_shapes.append(shape)
        except Exception:
            pass  # Not an image shape

    if not image_shapes:
        return

    used_shapes: set[int] = set()

    for zone in image_zones:
        # Determine the image path to use
        image_path = _resolve_image_for_zone(zone, slide_data, workspace_root)
        if not image_path:
            continue

        # Find the best-matching image shape by position
        zone_pos = zone.get("position", [0, 0, 0, 0])
        best_si, best_dist = None, float("inf")

        for si, shape in enumerate(image_shapes):
            if si in used_shapes:
                continue
            sx = (shape.left or 0) / SLIDE_W
            sy = (shape.top or 0) / SLIDE_H
            sw = (shape.width or 0) / SLIDE_W
            sh = (shape.height or 0) / SLIDE_H

            # Center-point distance
            z_cx = zone_pos[0] + zone_pos[2] / 2
            z_cy = zone_pos[1] + zone_pos[3] / 2
            s_cx = sx + sw / 2
            s_cy = sy + sh / 2
            dist = ((z_cx - s_cx) ** 2 + (z_cy - s_cy) ** 2) ** 0.5

            if dist < best_dist:
                best_dist = dist
                best_si = si

        if best_si is not None and best_dist < 0.5:
            # Replace the image
            _replace_shape_image(image_shapes[best_si], image_path)
            used_shapes.add(best_si)


def _resolve_image_for_zone(zone: dict, slide_data: dict,
                             workspace_root: Path | None) -> Path | None:
    """Find the image file for an image zone.

    Priority: user-uploaded image > generated image.
    """
    # User-uploaded image (from document analysis)
    image_ref = zone.get("image_ref")
    if image_ref:
        p = _resolve_image_path(str(image_ref), workspace_root)
        if p:
            return p

    # Generated image — use the output_name set during image gen config
    gen_name = zone.get("generated_image")
    if gen_name and workspace_root:
        bg_path = workspace_root / "background_images" / gen_name
        if bg_path.exists():
            return bg_path

    # Fallback: look for any generated image for this slide in background_images/
    slide_idx = slide_data.get("slide_index", 0)
    if workspace_root:
        bg_dir = workspace_root / "background_images"
        if bg_dir.exists():
            for pattern in [f"slide-{slide_idx:03d}.png",
                           f"slide_{slide_idx:03d}.png"]:
                p = bg_dir / pattern
                if p.exists():
                    return p

    return None


def _replace_shape_image(shape, image_path: Path) -> None:
    """Replace the picture data in an existing image shape.

    Uses the shape's picture part to swap out the image bytes while
    keeping the shape's position, size, and crop intact.
    """
    try:
        from pptx.opc.constants import RELATIONSHIP_TYPE as RT
        import io

        # Read new image
        with open(image_path, "rb") as f:
            new_blob = f.read()

        # Picture-filled AutoShapes have no ``shape.image`` property. Resolve
        # the embedded image relationship directly from their DrawingML blip
        # so the inherited geometry, rounded corners, crop and effects stay
        # intact.
        blips = shape._element.xpath('.//*[local-name()="blip"]')
        if not blips:
            return
        rId = blips[0].get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
        )
        if rId:
            rel = shape.part.rels[rId]
            rel.target_part._blob = new_blob
    except Exception:
        # Fallback: remove old shape, add new picture
        pass
