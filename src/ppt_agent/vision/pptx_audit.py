"""PPTX structural audit — checks visual quality from shape data.

Does NOT require rendered images — analyzes PPTX shapes directly:
- Font size readability checks
- Shape fill/text contrast ratio
- Text overflow detection
- Layout density / balance
- Color palette extraction from shapes
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from ppt_agent.vision.design_audit import (
    SlideAuditResult,
    ColorPalette,
    ContrastIssue,
    EdgeComplexity,
    LuminanceStats,
    DesignAudit,
    _hex_to_rgb,
    _contrast_ratio,
)

logger = logging.getLogger(__name__)

# 16:9 slide dimensions in EMU
SLIDE_W = 12192000
SLIDE_H = 6858000

# Minimum readable font sizes (in EMU, 1pt = 12700 EMU)
MIN_TITLE_FONT = 18 * 12700    # 18pt
MIN_BODY_FONT = 10 * 12700     # 10pt
MAX_BODY_FONT = 48 * 12700     # 48pt

# Max text length before overflow warning (chars per inch of shape)
MAX_CHARS_PER_INCH = 12  # rough estimate for 12pt font


@dataclass
class FontIssue:
    """Font readability or sizing problem."""
    level: str = "minor"  # critical | major | minor
    shape_type: str = ""
    font_size_pt: float = 0
    text: str = ""
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "shape_type": self.shape_type,
            "font_size_pt": self.font_size_pt,
            "text_preview": self.text[:40],
            "message": self.message,
        }


@dataclass
class OverflowIssue:
    """Text may overflow its container."""
    shape_type: str = ""
    shape_width_inch: float = 0
    text_chars: int = 0
    estimated_lines: int = 1
    available_height_inch: float = 0
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "shape_type": self.shape_type,
            "shape_width_inch": round(self.shape_width_inch, 1),
            "text_chars": self.text_chars,
            "estimated_lines": self.estimated_lines,
            "available_height_inch": round(self.available_height_inch, 1),
            "message": self.message,
        }


@dataclass
class PptxSlideAudit:
    """Audit results for a single PPTX slide (no image needed)."""
    slide_index: int = 0
    layout_name: str = ""
    shape_count: int = 0
    text_zones: list[dict] = field(default_factory=list)
    font_issues: list[FontIssue] = field(default_factory=list)
    overflow_issues: list[OverflowIssue] = field(default_factory=list)
    contrast_issues: list[ContrastIssue] = field(default_factory=list)
    palette: ColorPalette = field(default_factory=ColorPalette)
    score: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "slide_index": self.slide_index,
            "layout_name": self.layout_name,
            "shape_count": self.shape_count,
            "text_zones": self.text_zones,
            "font_issues": [f.to_dict() for f in self.font_issues],
            "overflow_issues": [o.to_dict() for o in self.overflow_issues],
            "contrast_issues": [c.to_dict() for c in self.contrast_issues],
            "palette": self.palette.to_dict(),
            "score": round(self.score, 1),
            "warnings": self.warnings,
        }


@dataclass
class PptxAuditReport:
    """Visual audit report for an entire PPTX file."""
    slides: list[PptxSlideAudit] = field(default_factory=list)
    aggregate_score: float = 0.0
    total_font_issues: int = 0
    total_overflow_issues: int = 0
    total_contrast_issues: int = 0
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "slides": [s.to_dict() for s in self.slides],
            "aggregate_score": round(self.aggregate_score, 1),
            "issues_summary": {
                "font": self.total_font_issues,
                "overflow": self.total_overflow_issues,
                "contrast": self.total_contrast_issues,
            },
            "recommendations": self.recommendations,
        }


def audit_pptx(pptx_path: Path | str) -> PptxAuditReport:
    """Run a structural visual audit on a PPTX file.

    Checks font sizes, text overflow, contrast, and color palette
    directly from PPTX shapes — no rendering required.

    Args:
        pptx_path: Path to the final.pptx file.

    Returns:
        PptxAuditReport with per-slide quality scores.
    """
    try:
        from pptx import Presentation
        prs = Presentation(str(pptx_path))
    except Exception as e:
        logger.warning("Cannot open PPTX for audit: %s", e)
        return PptxAuditReport(
            recommendations=["Cannot open PPTX file for visual audit."],
        )

    slide_results = []
    total_font = 0
    total_overflow = 0
    total_contrast = 0

    for slide_idx, slide in enumerate(prs.slides):
        result = _audit_slide(slide, slide_idx)
        slide_results.append(result)
        total_font += len(result.font_issues)
        total_overflow += len(result.overflow_issues)
        total_contrast += len(result.contrast_issues)

    n = max(len(slide_results), 1)
    aggregate = sum(s.score for s in slide_results) / n

    # Generate recommendations
    recommendations = []
    if total_font > 0:
        recommendations.append(f"修复 {total_font} 处字体大小问题")
    if total_overflow > 0:
        recommendations.append(f"修复 {total_overflow} 处文字溢出问题")
    if total_contrast > 0:
        recommendations.append(f"改进 {total_contrast} 处对比度问题")

    return PptxAuditReport(
        slides=slide_results,
        aggregate_score=aggregate,
        total_font_issues=total_font,
        total_overflow_issues=total_overflow,
        total_contrast_issues=total_contrast,
        recommendations=recommendations,
    )


def _audit_slide(slide, idx: int) -> PptxSlideAudit:
    """Audit a single slide."""
    # Layout name
    layout_name = slide.slide_layout.name if slide.slide_layout else "unknown"

    # Gather text shapes
    text_zones = []
    font_issues = []
    overflow_issues = []
    contrast_issues = []
    palette_colors: dict[str, float] = {}  # hex -> count

    for shape in slide.shapes:
        if not shape.has_text_frame:
            continue

        txt = shape.text_frame.text.strip()
        if not txt:
            continue

        # Position as fraction
        left = (shape.left or 0) / SLIDE_W
        top = (shape.top or 0) / SLIDE_H
        width = (shape.width or 0) / SLIDE_W
        height = (shape.height or 0) / SLIDE_H

        text_zones.append({
            "text": txt[:60],
            "position": [round(left, 3), round(top, 3), round(width, 3), round(height, 3)],
        })

        # ── Font size check ──
        _check_font_sizes(shape, txt, font_issues)

        # ── Text overflow check ──
        _check_overflow(shape, txt, width, height, overflow_issues)

        # ── Color / contrast ──
        _check_shape_contrast(shape, contrast_issues, palette_colors)

    # Build color palette from shape fills
    palette = _build_palette(palette_colors)

    # Score
    score = _compute_pptx_slide_score(font_issues, overflow_issues, contrast_issues)

    # Warnings
    warnings = []
    for f in font_issues[:3]:
        warnings.append(f.message)
    for o in overflow_issues[:2]:
        warnings.append(o.message)
    for c in contrast_issues[:2]:
        warnings.append(
            f"对比度: {c.foreground}/{c.background} = {c.ratio:.1f}:1"
        )

    return PptxSlideAudit(
        slide_index=idx,
        layout_name=layout_name,
        shape_count=len(text_zones),
        text_zones=text_zones,
        font_issues=font_issues,
        overflow_issues=overflow_issues,
        contrast_issues=contrast_issues,
        palette=palette,
        score=score,
        warnings=warnings[:5],  # cap at 5
    )


def _check_font_sizes(shape, txt: str, issues: list[FontIssue]) -> None:
    """Check font sizes for readability."""
    for para in shape.text_frame.paragraphs:
        font_size = para.font.size
        if font_size is None:
            # Try run-level font
            for run in para.runs:
                if run.font.size:
                    font_size = run.font.size
                    break
        if font_size is None:
            continue

        pt = font_size / 12700

        if pt < 8:
            issues.append(FontIssue(
                level="critical",
                shape_type="body",
                font_size_pt=pt,
                text=txt,
                message=f"字体过小 ({pt:.0f}pt)，难以阅读",
            ))
        elif pt < 10:
            issues.append(FontIssue(
                level="minor",
                shape_type="body",
                font_size_pt=pt,
                text=txt,
                message=f"字体偏小 ({pt:.0f}pt)，建议 ≥10pt",
            ))


def _check_overflow(shape, txt: str, width_frac: float, height_frac: float,
                    issues: list[OverflowIssue]) -> None:
    """Check if text likely overflows its shape.  Only reports clear overflows."""
    width_inch = width_frac * 13.333
    height_inch = height_frac * 7.5
    if width_inch < 0.5 or height_inch < 0.05:
        return  # shape too small for meaningful check

    # Estimate font size
    font_pt = 12  # default
    for para in shape.text_frame.paragraphs:
        for run in para.runs:
            if run.font.size:
                font_pt = run.font.size / 12700
                break
        if font_pt != 12:
            break

    # Chars per line (Chinese chars are ~2x wider than Latin)
    chars_per_inch = 72 / font_pt * 0.35  # conservative for CJK text
    chars_per_line = max(1, int(width_inch * chars_per_inch))

    # Total chars and estimated lines
    total_chars = len(txt)
    para_count = len(shape.text_frame.paragraphs)

    # Estimate lines: each paragraph at least 1 line
    estimated_lines = 0
    remaining_chars = total_chars
    for _ in range(para_count):
        line_chars = min(remaining_chars, chars_per_line)
        estimated_lines += max(1, math.ceil(line_chars / chars_per_line))
        remaining_chars -= line_chars
        if remaining_chars <= 0:
            break
    if remaining_chars > 0:
        estimated_lines += math.ceil(remaining_chars / chars_per_line)

    # Available lines
    line_height_inch = font_pt / 72 * 1.5  # generous line spacing for CJK
    available_lines = max(1, int(height_inch / line_height_inch))

    # Only report if text CLEARLY overflows (needs >2x available space)
    if estimated_lines > available_lines * 2.0:
        issues.append(OverflowIssue(
            shape_type="body",
            shape_width_inch=round(width_inch, 1),
            text_chars=total_chars,
            estimated_lines=estimated_lines,
            available_height_inch=round(height_inch, 1),
            message=f"文字可能溢出: {total_chars}字 需要~{estimated_lines}行 可用{available_lines}行",
        ))


import math


def _check_shape_contrast(shape, issues: list[ContrastIssue],
                          palette: dict[str, float]) -> None:
    """Check contrast between shape fill and text color."""
    try:
        fill_rgb = _get_shape_fill_color(shape)
        text_rgb = _get_shape_text_color(shape)
    except Exception:
        return

    if fill_rgb is None or text_rgb is None:
        return

    # Track palette
    hex_fill = "#{:02X}{:02X}{:02X}".format(*fill_rgb)
    palette[hex_fill] = palette.get(hex_fill, 0) + 1

    ratio = _contrast_ratio(fill_rgb, text_rgb)

    if ratio < 3.0:
        issues.append(ContrastIssue(
            level="critical",
            element="text-on-background",
            foreground="#{:02X}{:02X}{:02X}".format(*text_rgb),
            background=hex_fill,
            ratio=ratio,
            required=4.5,
        ))
    elif ratio < 4.5:
        issues.append(ContrastIssue(
            level="major",
            element="text-on-background",
            foreground="#{:02X}{:02X}{:02X}".format(*text_rgb),
            background=hex_fill,
            ratio=ratio,
            required=4.5,
        ))


def _get_shape_fill_color(shape) -> tuple[int, int, int] | None:
    """Extract fill color from a shape (including inherited from layout)."""
    try:
        fill = shape.fill
        if fill.type is not None:
            if hasattr(fill, 'fore_color') and fill.fore_color.type is not None:
                return (
                    fill.fore_color.rgb[0],
                    fill.fore_color.rgb[1],
                    fill.fore_color.rgb[2],
                )
    except Exception:
        pass

    # Try to get background from slide layout
    try:
        layout = shape.part.slide_layout if hasattr(shape, 'part') else None
        if layout is not None and hasattr(layout, 'background'):
            bg = layout.background
            if bg.fill.type is not None:
                return (
                    bg.fill.fore_color.rgb[0],
                    bg.fill.fore_color.rgb[1],
                    bg.fill.fore_color.rgb[2],
                )
    except Exception:
        pass

    return None


def _get_shape_text_color(shape) -> tuple[int, int, int] | None:
    """Extract text color from the first run of a shape (including inherited)."""
    try:
        for para in shape.text_frame.paragraphs:
            for run in para.runs:
                if run.font.color and run.font.color.type is not None:
                    return (
                        run.font.color.rgb[0],
                        run.font.color.rgb[1],
                        run.font.color.rgb[2],
                    )
            # Check paragraph-level font
            if para.font.color and para.font.color.type is not None:
                return (
                    para.font.color.rgb[0],
                    para.font.color.rgb[1],
                    para.font.color.rgb[2],
                )
    except Exception:
        pass

    # Fallback: check theme/slide master common text colors
    # Most templates use dark text, so default to #333333
    return (51, 51, 51)  # Default dark gray


def _build_palette(color_counts: dict[str, float]) -> ColorPalette:
    """Build a color palette from shape fill color frequencies."""
    total = sum(color_counts.values()) or 1
    colors = []
    for hex_color, count in sorted(color_counts.items(), key=lambda x: -x[1]):
        colors.append({
            "hex": hex_color,
            "pct": round(count / total, 3),
            "name": _guess_color_name_hex(hex_color),
        })
    return ColorPalette(colors=colors[:5])


def _guess_color_name_hex(hex_color: str) -> str:
    """Guess a color name from a hex string."""
    from ppt_agent.vision.design_audit import _guess_color_names
    rgb = _hex_to_rgb(hex_color)
    names = _guess_color_names([[rgb[0], rgb[1], rgb[2]]])
    return names[0] if names else "unknown"


def _compute_pptx_slide_score(
    font_issues: list,
    overflow_issues: list,
    contrast_issues: list,
) -> float:
    """Compute slide quality score (0-100)."""
    score = 80.0  # baseline

    for f in font_issues:
        if f.level == "critical":
            score -= 15
        else:
            score -= 5

    for _ in overflow_issues:
        score -= 3

    for c in contrast_issues:
        if c.level == "critical":
            score -= 15
        elif c.level == "major":
            score -= 8
        else:
            score -= 3

    return max(0, min(100, score))
