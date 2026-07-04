"""OCR module — extract text regions from slide images using Tesseract.

Returns bounding-box regions with text content, normalised to fractional
coordinates (0-1) relative to image dimensions.  This lets downstream
code map OCR results directly onto PPT slide coordinates.

Requires:
    - pytesseract (Python binding)
    - tesseract binary (``/opt/homebrew/bin/tesseract`` or on PATH)
    - Pillow for image loading
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

try:
    import pytesseract
except ImportError:
    pytesseract = None  # type: ignore[assignment]


@dataclass
class OCRRegion:
    """A detected text region on a slide image."""

    text: str
    x: float       # left edge, 0-1 fraction of image width
    y: float       # top edge, 0-1 fraction of image height
    w: float       # width, 0-1 fraction
    h: float       # height, 0-1 fraction
    confidence: float  # 0-100

    def to_dict(self) -> dict:
        return asdict(self)


def ocr_slide_image(
    path: Path,
    *,
    lang: str = "chi_sim+eng",
    min_confidence: float = 30.0,
    merge_lines: bool = True,
) -> list[OCRRegion]:
    """Run OCR on a single slide image and return detected text regions.

    Parameters
    ----------
    path
        Path to the slide image (PNG/JPG).
    lang
        Tesseract language string.
    min_confidence
        Drop detections below this confidence (0-100).
    merge_lines
        If True, merge word-level boxes into line-level regions.

    Returns an empty list when tesseract is unavailable or the image
    cannot be read (graceful degradation).
    """
    if pytesseract is None:
        logger.warning("pytesseract not installed — OCR unavailable")
        return []

    path = Path(path)
    if not path.exists():
        logger.warning("OCR image not found: %s", path)
        return []

    try:
        img = Image.open(path)
        img_w, img_h = img.size
    except Exception as exc:
        logger.warning("Failed to open image %s: %s", path, exc)
        return []

    if img_w == 0 or img_h == 0:
        return []

    try:
        data = pytesseract.image_to_data(
            img, lang=lang, output_type=pytesseract.Output.DICT,
        )
    except Exception as exc:
        logger.warning("Tesseract failed on %s: %s", path, exc)
        return []

    # Build word-level regions
    words: list[OCRRegion] = []
    n_boxes = len(data["text"])
    for i in range(n_boxes):
        text = str(data["text"][i]).strip()
        if not text:
            continue
        conf = float(data["conf"][i])
        if conf < min_confidence:
            continue
        words.append(OCRRegion(
            text=text,
            x=data["left"][i] / img_w,
            y=data["top"][i] / img_h,
            w=data["width"][i] / img_w,
            h=data["height"][i] / img_h,
            confidence=conf,
        ))

    if not words:
        return []

    if not merge_lines:
        return words

    # Merge words into lines: group by similar y-position (within 2% of image height)
    return _merge_into_lines(words)


def _merge_into_lines(words: list[OCRRegion], y_tolerance: float = 0.02) -> list[OCRRegion]:
    """Merge word boxes into line-level regions based on y-proximity."""
    if not words:
        return []

    # Sort by y then x
    sorted_words = sorted(words, key=lambda r: (r.y, r.x))
    lines: list[list[OCRRegion]] = []
    current_line: list[OCRRegion] = [sorted_words[0]]

    for word in sorted_words[1:]:
        last = current_line[-1]
        # Same line if y-centers are within tolerance
        last_center_y = last.y + last.h / 2
        word_center_y = word.y + word.h / 2
        if abs(word_center_y - last_center_y) <= y_tolerance:
            current_line.append(word)
        else:
            lines.append(current_line)
            current_line = [word]
    lines.append(current_line)

    # Merge each line into a single region
    merged: list[OCRRegion] = []
    for line_words in lines:
        texts = [w.text for w in line_words]
        x_min = min(w.x for w in line_words)
        y_min = min(w.y for w in line_words)
        x_max = max(w.x + w.w for w in line_words)
        y_max = max(w.y + w.h for w in line_words)
        avg_conf = sum(w.confidence for w in line_words) / len(line_words)
        merged.append(OCRRegion(
            text=" ".join(texts),
            x=x_min, y=y_min,
            w=x_max - x_min, h=y_max - y_min,
            confidence=avg_conf,
        ))

    return merged


def classify_zones(
    regions: list[OCRRegion],
    slide_index: int = 0,
) -> list[dict]:
    """Classify OCR regions into zone types (title / body / footer / decoration).

    Heuristics:
    - Top 20% of slide → likely title
    - Bottom 15% of slide → likely footer
    - Large font (tall region relative to its text length) → title candidate
    - Everything else → body text (bullets)

    Returns a list of zone dicts with: zone_id, type, position [x,y,w,h],
    text, confidence.
    """
    if not regions:
        return []

    zones: list[dict] = []
    for i, r in enumerate(regions):
        center_y = r.y + r.h / 2

        if center_y < 0.20 and r.h > 0.04:
            zone_type = "title"
        elif center_y > 0.85:
            zone_type = "footer"
        elif r.w < 0.15 and r.h < 0.04:
            zone_type = "decoration"
        else:
            zone_type = "body"

        zones.append({
            "zone_id": f"s{slide_index}_z{i}",
            "type": zone_type,
            "position": [round(r.x, 4), round(r.y, 4), round(r.w, 4), round(r.h, 4)],
            "text": r.text,
            "confidence": round(r.confidence, 1),
        })

    return zones


# Legacy interface kept for backward compat
def ocr_image(path: Path) -> tuple[str, list[str]]:
    """Legacy OCR function — returns (full_text, warnings)."""
    regions = ocr_slide_image(path)
    if not regions:
        return "", [f"OCR returned no text for {path.name}"]
    full_text = "\n".join(r.text for r in regions)
    return full_text, []
