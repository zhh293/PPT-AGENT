"""Vision analysis module for PPT-Agent.

Provides pure-Python design analysis tools that don't require
external AI models — K-means color extraction, Sobel edge detection,
WCAG 2.1 contrast checks, and luminance statistics.

Also provides a bridge to the ai-vision-mcp CLI for AI-powered
analysis when a vision model API key is configured.
"""

from ppt_agent.vision.design_audit import (
    DesignAudit,
    audit_slide_image,
    audit_template,
    audit_pptx_slides,
    ColorPalette,
    ContrastIssue,
    EdgeComplexity,
    LuminanceStats,
)

__all__ = [
    "DesignAudit",
    "audit_slide_image",
    "audit_template",
    "audit_pptx_slides",
    "ColorPalette",
    "ContrastIssue",
    "EdgeComplexity",
    "LuminanceStats",
]
