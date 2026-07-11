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
        # Template formatting with size fallback
        _apply_zone_formatting(p, zone, default_size_pt=title_font_size(text), default_color_hex="FFFFFF")
        if not (zone.get("formatting", {}) or {}).get("font_size_pt"):
            font.size = Pt(title_font_size(text))
        font.bold = True
        # White text for readability on image backgrounds (unless template says otherwise)
        if not (zone.get("formatting", {}) or {}).get("font_color"):
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
            _apply_zone_formatting(p, zone, default_size_pt=font_sz, default_color_hex="FFFFFF")
            if not (zone.get("formatting", {}) or {}).get("font_size_pt"):
                font.size = Pt(font_sz)
            if not (zone.get("formatting", {}) or {}).get("font_color"):
                font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            _add_text_shadow(p)

    elif zone_type == "footer":
        text = str(content)
        p = tf.paragraphs[0]
        p.text = text
        p.alignment = PP_ALIGN.RIGHT
        _apply_zone_formatting(p, zone, default_size_pt=12, default_color_hex="CCCCCC")

    elif zone_type == "subtitle":
        text = str(content)
        p = tf.paragraphs[0]
        p.text = text
        p.alignment = PP_ALIGN.LEFT
        _apply_zone_formatting(p, zone, default_size_pt=20, default_color_hex="EEEEEE")
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

    # Delete original template slides (keep only cloned + overlaid ones)
    _delete_slides_by_id(prs, original_slide_ids)

    prs.save(str(output_path))
    return output_path


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
    tf = shape.text_frame
    zone_type = zone.get("type", "body")
    content = zone.get("content", "")

    # Normalize content to list
    if isinstance(content, list):
        items = [str(c) for c in content]
    else:
        items = [str(content)]

    para_count = len(tf.paragraphs)

    # Write content to existing paragraphs
    for i, item in enumerate(items):
        if i < para_count:
            p = tf.paragraphs[i]
        else:
            p = tf.add_paragraph()
        p.text = str(item)

    # Clear leftover paragraphs (remove old template text)
    for i in range(len(items), para_count):
        tf.paragraphs[i].text = ""


def _clear_shape_text(shape) -> None:
    """Clear all text from a shape (removes template placeholder text)."""
    if not shape.has_text_frame:
        return
    for p in shape.text_frame.paragraphs:
        p.text = ""
