"""Visual zone refinement — use pixel data from slide images to improve
zone classification in meta.json.

Runs AFTER shapes are extracted from PPTX.  Uses the rendered slide image
(exported via LibreOffice) to:
1. Validate zone type against visual appearance (title shapes should
   actually look like titles in the image).
2. Identify content regions vs decorative regions by analyzing text
   density, edge density, and color distribution.
3. Correct misclassified zones — e.g. a shape marked as 'title' that
   actually contains tiny decorative text gets downgraded to 'decoration'.

This is the bridge between PPTX-structural analysis and visual perception.
Pure Python, no AI model required.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class VisualZoneInfo:
    """Per-zone information derived from pixel analysis."""
    zone_id: str = ""
    mean_brightness: float = 128.0
    edge_density: float = 0.0       # edges within the zone area
    text_likeness: float = 0.0       # how much it looks like a text block
    dominant_hex: str = "#808080"    # dominant color in this zone
    suggested_type: str = ""         # what the visual data suggests
    is_content_area: bool = True     # looks like it should hold content

    def to_dict(self) -> dict:
        return {
            "zone_id": self.zone_id,
            "mean_brightness": round(self.mean_brightness, 1),
            "edge_density": round(self.edge_density, 4),
            "text_likeness": round(self.text_likeness, 3),
            "dominant_hex": self.dominant_hex,
            "suggested_type": self.suggested_type,
            "is_content_area": self.is_content_area,
        }


@dataclass
class SlideVisualRefinement:
    slide_index: int = 0
    zones: list[VisualZoneInfo] = field(default_factory=list)
    global_brightness: float = 128.0
    content_region_found: bool = True

    def to_dict(self) -> dict:
        return {
            "slide_index": self.slide_index,
            "zones": [z.to_dict() for z in self.zones],
            "global_brightness": round(self.global_brightness, 1),
            "content_region_found": self.content_region_found,
        }


# ── Main entry point ──────────────────────────────────────────────────


def refine_zones_with_vision(
    slide_zones: list[dict],
    slide_image_path: Path | str | None,
    slide_index: int = 0,
) -> list[dict]:
    """Refine zone classification using pixel data from the slide image.

    Args:
        slide_zones: Current zone definitions from PPTX shape analysis.
        slide_image_path: Path to the rendered slide PNG (or None).
        slide_index: Slide number for logging.

    Returns:
        Updated zone list with corrected types and added visual info.
    """
    if slide_image_path is None or not Path(slide_image_path).exists():
        logger.debug("No slide image for slide %d — skipping visual refinement", slide_index)
        return slide_zones

    img, w, h = _load_image(slide_image_path)
    if img is None:
        return slide_zones

    try:
        import numpy as np
        pixels = np.array(img)  # (h, w, 3) RGB
    except ImportError:
        return slide_zones

    # Analyze each zone in the image
    for zone in slide_zones:
        pos = zone.get("position", [0, 0, 0, 0])
        visual = _analyze_zone_region(pixels, w, h, pos)
        visual.zone_id = zone.get("zone_id", "")

        # Store visual info
        zone["visual"] = visual.to_dict()

        # ── Correct zone type based on visual evidence ──
        zone["type"] = _correct_zone_type(zone["type"], visual, zone.get("formatting", {}))

    return slide_zones


# ── Image Loading ─────────────────────────────────────────────────────


def _load_image(image_path: Path | str):
    """Load an image via Pillow."""
    try:
        from PIL import Image
        img = Image.open(image_path).convert("RGB")
        return img, img.width, img.height
    except Exception:
        logger.warning("Cannot load image: %s", image_path)
        return None, 0, 0


# ── Zone Region Analysis ──────────────────────────────────────────────


def _analyze_zone_region(
    pixels,      # numpy array (h, w, 3)
    img_w: int,
    img_h: int,
    position: list[float],
) -> VisualZoneInfo:
    """Extract pixel-level features from a zone's bounding box in the image."""
    x_frac, y_frac, w_frac, h_frac = position

    # Convert fractional position to pixel coordinates
    x1 = max(0, int(x_frac * img_w))
    y1 = max(0, int(y_frac * img_h))
    x2 = min(img_w, int((x_frac + w_frac) * img_w))
    y2 = min(img_h, int((y_frac + h_frac) * img_h))

    if x2 <= x1 or y2 <= y1:
        return VisualZoneInfo(is_content_area=False)

    # Extract the region
    region = pixels[y1:y2, x1:x2]

    # ── Mean brightness ──
    mean_brightness = float(region.mean())

    # ── Edge density ──
    edge_density = _compute_region_edge_density(region)

    # ── Text likeness ──
    text_likeness = _estimate_text_likeness(region)

    # ── Dominant color ──
    dominant_hex = _dominant_region_color(region)

    # ── Suggested type ──
    suggested_type = _suggest_type_from_visual(
        text_likeness, edge_density, mean_brightness, w_frac, h_frac, y_frac
    )

    # ── Content area? ──
    is_content = not (
        edge_density < 0.01                         # blank area
        or (text_likeness < 0.05 and w_frac < 0.05) # tiny decoration
        or (y_frac > 0.88 and text_likeness < 0.2)  # footer
    )

    return VisualZoneInfo(
        mean_brightness=mean_brightness,
        edge_density=edge_density,
        text_likeness=text_likeness,
        dominant_hex=dominant_hex,
        suggested_type=suggested_type,
        is_content_area=is_content,
    )


def _compute_region_edge_density(region) -> float:
    """Sobel edge density for a region."""
    try:
        import numpy as np
        if region.size < 100:
            return 0.0
        gray = np.mean(region, axis=2).astype(np.float32)
        if gray.shape[0] < 3 or gray.shape[1] < 3:
            return 0.0
        gx = np.abs(gray[1:-1, 2:] - gray[1:-1, :-2])
        gy = np.abs(gray[2:, 1:-1] - gray[:-2, 1:-1])
        mag = (gx + gy) / 2
        return float(np.mean(mag) / 255)
    except Exception:
        return 0.0


def _estimate_text_likeness(region) -> float:
    """Estimate how likely this region contains text.

    Text regions typically have:
    - High local contrast (sharp edges within small areas)
    - Bimodal brightness distribution (text vs background)
    - Moderate edge density
    """
    try:
        import numpy as np
        if region.size < 100:
            return 0.0

        gray = region.mean(axis=2)

        # Bimodality: high std_dev suggests text+background separation
        std = float(np.std(gray))
        std_score = min(1.0, std / 60)

        # Sharp transitions: edge pixels as fraction
        if gray.shape[0] >= 3 and gray.shape[1] >= 3:
            gx = np.abs(gray[1:-1, 2:] - gray[1:-1, :-2])
            gy = np.abs(gray[2:, 1:-1] - gray[:-2, 1:-1])
            edge_frac = float(np.mean((gx + gy) > 30))
        else:
            edge_frac = 0

        # Text has moderate edge density (not too low, not too high like images)
        edge_score = 1.0 - abs(edge_frac - 0.08) / 0.15
        edge_score = max(0, min(1, edge_score))

        return (std_score * 0.6 + edge_score * 0.4)
    except Exception:
        return 0.0


def _dominant_region_color(region) -> str:
    """Get the average color of a region as hex."""
    try:
        import numpy as np
        avg = region.mean(axis=(0, 1))
        return "#{:02X}{:02X}{:02X}".format(
            int(avg[0]), int(avg[1]), int(avg[2]),
        )
    except Exception:
        return "#808080"


def _suggest_type_from_visual(
    text_likeness: float,
    edge_density: float,
    brightness: float,
    w: float, h: float, y: float,
) -> str:
    """Suggest a zone type based on visual features alone."""
    # Blank → decoration
    if edge_density < 0.01:
        return "decoration"

    # High text likeness + top of slide → title
    if text_likeness > 0.3 and y < 0.30 and w > 0.15:
        return "title"

    # High text likeness + mid slide → body
    if text_likeness > 0.2 and 0.20 < y < 0.85:
        return "body"

    # Image-like (high edge density, low text likeness)
    if edge_density > 0.06 and text_likeness < 0.15:
        return "image"

    # Footer
    if y > 0.85:
        return "footer"

    return "body"


# ── Type Correction ───────────────────────────────────────────────────


def _correct_zone_type(
    current_type: str,
    visual: VisualZoneInfo,
    formatting: dict,
) -> str:
    """Correct zone type using visual evidence.

    Applies both downgrades and upgrades based on visual features.
    """
    # ── Decoration stays decoration ──
    if current_type == "decoration":
        return current_type

    # ── Image → text upgrade (visual says text, PPTX structural data is wrong) ──
    if current_type == "image" and visual.text_likeness > 0.4:
        return "body"

    # ── Image with no visual content → decoration ──
    if current_type == "image" and visual.edge_density < 0.01:
        return "decoration"

    # ── Title → decoration downgrades ──
    if current_type == "title":
        if visual.text_likeness < 0.05:
            return "decoration"
        if not visual.is_content_area:
            return "decoration"

    # ── Body → decoration downgrades ──
    if current_type == "body":
        if visual.edge_density < 0.005 and visual.text_likeness < 0.03:
            return "decoration"

    return current_type
