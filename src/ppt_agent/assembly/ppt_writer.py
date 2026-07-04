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
        font.size = Pt(title_font_size(text))
        font.bold = True
        # White text for readability on image backgrounds
        font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        # Add shadow effect for legibility
        _add_text_shadow(p)

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

            # Bullet character
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
            font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            _add_text_shadow(p)

    elif zone_type == "footer":
        text = str(content)
        p = tf.paragraphs[0]
        p.text = text
        p.alignment = PP_ALIGN.RIGHT
        font = p.font
        font.size = Pt(12)
        font.color.rgb = RGBColor(0xCC, 0xCC, 0xCC)

    elif zone_type == "subtitle":
        text = str(content)
        p = tf.paragraphs[0]
        p.text = text
        p.alignment = PP_ALIGN.LEFT
        font = p.font
        font.size = Pt(20)
        font.color.rgb = RGBColor(0xEE, 0xEE, 0xEE)
        _add_text_shadow(p)


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


def _add_title_zone(slide, zone: dict) -> None:
    """Add a title text box (solid background mode)."""
    text = str(zone.get("content") or "")
    left, top, width, height = _emu_rect(zone["position"])
    txbox = slide.shapes.add_textbox(Emu(left), Emu(top), Emu(width), Emu(height))
    tf = txbox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = text
    p.alignment = PP_ALIGN.LEFT
    font = p.font
    font.size = Pt(title_font_size(text))
    font.bold = True
    font.color.rgb = RGBColor(0x33, 0x33, 0x33)


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
        font.color.rgb = RGBColor(0x44, 0x44, 0x44)


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
        elif zone_type == "image" and not bg_path:
            # Only add separate images in solid-background mode
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
            for pattern in [f"slide_{idx:02d}.png", f"slide_{idx}.png"]:
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
    output_path: Path,
    workspace_root: Path | None = None,
) -> None:
    """Write a PPTX file from *slide_contents*.

    Automatically chooses between:
    - Background-image mode (when background images are available)
    - Legacy solid-background mode (when no background images exist)

    Each slide can independently use either mode.
    """
    slides = slide_contents.get("slides", [])
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    prs = Presentation()
    prs.slide_width = Emu(SLIDE_W)
    prs.slide_height = Emu(SLIDE_H)

    for index, slide_data in enumerate(slides, start=1):
        _build_slide_with_background(prs, slide_data, index, workspace_root)

    prs.save(str(output_path))
