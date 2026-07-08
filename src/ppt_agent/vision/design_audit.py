"""Pure-Python design audit — zero AI model required.

Re-implements the key analysis tools from ai-vision-mcp's audit_design
directly in Python using Pillow + NumPy:

- K-means dominant color extraction (5 clusters)
- Sobel edge detection for layout complexity
- WCAG 2.1 AA/AAA contrast ratio checks
- Luminance statistics (mean brightness, std dev)

These run in the PPT-Agent pipeline without any external API calls.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Data Classes ──────────────────────────────────────────────────────


@dataclass
class ColorPalette:
    """Dominant colors extracted from a slide/image."""
    colors: list[dict] = field(default_factory=list)
    # Each color: {"hex": "#1F4E79", "pct": 0.35, "name": "dark blue"}

    def to_dict(self) -> dict:
        return {"dominant_colors": self.colors}


@dataclass
class ContrastIssue:
    """A WCAG contrast violation."""
    level: str = ""          # "critical" | "major" | "minor"
    element: str = ""        # "text" | "background" | "border"
    foreground: str = ""     # hex
    background: str = ""     # hex
    ratio: float = 0.0
    required: float = 4.5

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "element": self.element,
            "foreground": self.foreground,
            "background": self.background,
            "ratio": round(self.ratio, 2),
            "required": self.required,
        }


@dataclass
class EdgeComplexity:
    """Layout complexity from Sobel edge detection."""
    level: str = "moderate"  # "simple" | "moderate" | "complex"
    edge_density: float = 0.0
    horizontal_vs_vertical: float = 1.0

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "edge_density": round(self.edge_density, 4),
            "horizontal_vs_vertical": round(self.horizontal_vs_vertical, 2),
        }


@dataclass
class LuminanceStats:
    """Brightness statistics."""
    mean: float = 128.0
    std_dev: float = 0.0
    is_dark: bool = False
    is_light: bool = False

    def to_dict(self) -> dict:
        return {
            "mean_brightness": round(self.mean, 1),
            "std_dev": round(self.std_dev, 1),
            "is_dark": self.is_dark,
            "is_light": self.is_light,
        }


@dataclass
class SlideAuditResult:
    slide_index: int = 0
    colors: ColorPalette = field(default_factory=ColorPalette)
    contrast_issues: list[ContrastIssue] = field(default_factory=list)
    complexity: EdgeComplexity = field(default_factory=EdgeComplexity)
    luminance: LuminanceStats = field(default_factory=LuminanceStats)
    overall_score: float = 0.0  # 0-100
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "slide_index": self.slide_index,
            "colors": self.colors.to_dict(),
            "contrast_issues": [c.to_dict() for c in self.contrast_issues],
            "complexity": self.complexity.to_dict(),
            "luminance": self.luminance.to_dict(),
            "overall_score": round(self.overall_score, 1),
            "warnings": self.warnings,
        }


@dataclass
class DesignAudit:
    """Full audit report for a presentation."""
    slide_results: list[SlideAuditResult] = field(default_factory=list)
    aggregate_score: float = 0.0
    critical_issues: int = 0
    major_issues: int = 0
    minor_issues: int = 0
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "slides": [s.to_dict() for s in self.slide_results],
            "aggregate_score": round(self.aggregate_score, 1),
            "issues": {
                "critical": self.critical_issues,
                "major": self.major_issues,
                "minor": self.minor_issues,
            },
            "recommendations": self.recommendations,
        }


# ── Image Loading ─────────────────────────────────────────────────────


def _load_image(image_path: Path | str) -> tuple[Any, int, int]:
    """Load an image and return (pixels, width, height).

    Returns (None, 0, 0) if the image cannot be loaded.
    """
    try:
        from PIL import Image
        img = Image.open(image_path).convert("RGB")
        return img, img.width, img.height
    except ImportError:
        logger.warning("Pillow not installed — image analysis skipped")
        return None, 0, 0
    except Exception:
        logger.warning("Cannot load image: %s", image_path, exc_info=True)
        return None, 0, 0


# ── K-Means Color Extraction ──────────────────────────────────────────


def _extract_dominant_colors(img, k: int = 5) -> list[dict]:
    """Extract k dominant colors from an image using k-means.

    Falls back to a simple histogram if numpy/scipy are not available.
    """
    try:
        import numpy as np
        pixels = np.array(img, dtype=np.float32).reshape(-1, 3)

        # Sample pixels for speed (every 10th pixel)
        sample = pixels[::10]
        if len(sample) < k:
            sample = pixels

        # Simple k-means implementation (no sklearn dependency)
        centroids = _kmeans(sample, k)
    except ImportError:
        # Fallback: just use quantized colors
        try:
            q = img.quantize(colors=k, method=2)  # Median cut
            palette = q.getpalette()[: k * 3]
            centroids = []
            for i in range(k):
                r = palette[i * 3]
                g = palette[i * 3 + 1]
                b = palette[i * 3 + 2]
                centroids.append([r, g, b])
        except Exception:
            return [{"hex": "#808080", "pct": 1.0, "name": "gray"}]

    total_pixels = img.width * img.height
    if total_pixels == 0:
        return []

    # Count how many pixels belong to each centroid
    try:
        import numpy as np
        pixels_np = np.array(img, dtype=np.float32).reshape(-1, 3)
        counts = [0] * k
        for p in pixels_np[::10]:
            dists = [sum((p - c) ** 2) for c in centroids]
            counts[dists.index(min(dists))] += 1
        total_count = sum(counts) or 1
    except ImportError:
        counts = [1] * k
        total_count = k

    # Build result
    color_names = _guess_color_names(centroids)
    result = []
    for i, centroid in enumerate(centroids):
        hex_color = "#{:02X}{:02X}{:02X}".format(
            int(min(255, max(0, centroid[0]))),
            int(min(255, max(0, centroid[1]))),
            int(min(255, max(0, centroid[2]))),
        )
        result.append({
            "hex": hex_color,
            "pct": round(counts[i] / total_count, 3),
            "name": color_names[i] if i < len(color_names) else "unknown",
        })

    # Sort by percentage descending
    result.sort(key=lambda c: c["pct"], reverse=True)
    return result


def _kmeans(pixels, k: int, max_iter: int = 10) -> list:
    """Simple k-means clustering without sklearn."""
    import numpy as np

    # Initialize centroids by sampling
    indices = np.random.choice(len(pixels), k, replace=False)
    centroids = pixels[indices].copy()

    for _ in range(max_iter):
        # Assign pixels to nearest centroid
        labels = []
        for p in pixels:
            dists = [np.sum((p - c) ** 2) for c in centroids]
            labels.append(dists.index(min(dists)))

        # Update centroids
        new_centroids = []
        for i in range(k):
            cluster = [pixels[j] for j, lbl in enumerate(labels) if lbl == i]
            if cluster:
                new_centroids.append(np.mean(cluster, axis=0))
            else:
                new_centroids.append(centroids[i])

        # Check convergence
        max_shift = max(
            np.sqrt(np.sum((new_centroids[i] - centroids[i]) ** 2))
            for i in range(k)
        )
        centroids = new_centroids
        if max_shift < 1.0:
            break

    return [c.tolist() for c in centroids]


def _guess_color_names(centroids: list) -> list[str]:
    """Guess human-readable color names from RGB values."""
    names = []
    for rgb in centroids:
        r, g, b = rgb[0], rgb[1], rgb[2]
        max_c, min_c = max(r, g, b), min(r, g, b)
        delta = max_c - min_c
        lightness = (max_c + min_c) / 2

        if delta < 30:
            if lightness > 200:
                names.append("white")
            elif lightness < 50:
                names.append("dark")
            elif lightness < 120:
                names.append("gray")
            else:
                names.append("neutral")
        elif max_c == r:
            if g > 160:
                names.append("orange")
            elif g > 100:
                names.append("pink")
            else:
                names.append("red")
        elif max_c == g:
            names.append("green")
        elif max_c == b:
            names.append("blue")
        else:
            names.append("unknown")
    return names


# ── Sobel Edge Detection ──────────────────────────────────────────────


def _compute_edge_complexity(img) -> EdgeComplexity:
    """Compute layout complexity using Sobel edge detection.

    Returns an EdgeComplexity with level and edge_density.
    """
    try:
        import numpy as np
        gray = np.array(img.convert("L"), dtype=np.float32)

        # Simple Sobel-like edge detection (3x3 kernel)
        h, w = gray.shape

        # Horizontal gradient
        gx = np.zeros_like(gray)
        gx[1:-1, 1:-1] = (
            gray[2:, 1:-1] - gray[:-2, 1:-1] +
            2 * gray[2:, :-2] - 2 * gray[:-2, :-2] +
            gray[2:, 2:] - gray[:-2, 2:]
        )

        # Vertical gradient
        gy = np.zeros_like(gray)
        gy[1:-1, 1:-1] = (
            gray[1:-1, 2:] - gray[1:-1, :-2] +
            2 * gray[:-2, 2:] - 2 * gray[:-2, :-2] +
            gray[2:, 2:] - gray[2:, :-2]
        )

        # Gradient magnitude
        mag = np.sqrt(gx ** 2 + gy ** 2)

        # Edge density: fraction of pixels above threshold
        threshold = 50
        edge_density = float(np.sum(mag > threshold) / (h * w))

        # Horizontal vs vertical ratio
        h_strength = float(np.sum(np.abs(gx)))
        v_strength = float(np.sum(np.abs(gy)))
        hv_ratio = h_strength / v_strength if v_strength > 0 else 1.0

        # Classify
        if edge_density < 0.05:
            level = "simple"
        elif edge_density < 0.15:
            level = "moderate"
        else:
            level = "complex"

        return EdgeComplexity(
            level=level,
            edge_density=edge_density,
            horizontal_vs_vertical=round(hv_ratio, 2),
        )
    except ImportError:
        logger.warning("NumPy not available — edge detection skipped")
        return EdgeComplexity(level="moderate", edge_density=0.08, horizontal_vs_vertical=1.0)


# ── WCAG 2.1 Contrast ─────────────────────────────────────────────────


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    """Compute relative luminance per WCAG 2.1 definition."""
    def _linearize(c):
        s = c / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r_lin = _linearize(rgb[0])
    g_lin = _linearize(rgb[1])
    b_lin = _linearize(rgb[2])
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def _contrast_ratio(rgb1: tuple, rgb2: tuple) -> float:
    """Compute WCAG contrast ratio between two colors."""
    l1 = _relative_luminance(rgb1)
    l2 = _relative_luminance(rgb2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Parse a hex color string."""
    hex_color = hex_color.lstrip("#")
    return (
        int(hex_color[0:2], 16),
        int(hex_color[2:4], 16),
        int(hex_color[4:6], 16),
    )


def _check_contrast_issues(palette: ColorPalette) -> list[ContrastIssue]:
    """Check contrast between dominant colors for WCAG compliance."""
    issues = []
    colors = palette.colors
    if len(colors) < 2:
        return issues

    # Check each color pair
    for i in range(min(len(colors), 5)):
        for j in range(i + 1, min(len(colors), 5)):
            c1 = colors[i]
            c2 = colors[j]

            # Only check pairs where one is likely text and one is background
            # Light color + dark color = potential text/background pair
            if c1["pct"] < 0.05 or c2["pct"] < 0.05:
                continue

            try:
                rgb1 = _hex_to_rgb(c1["hex"])
                rgb2 = _hex_to_rgb(c2["hex"])
            except (ValueError, KeyError):
                continue

            ratio = _contrast_ratio(rgb1, rgb2)

            # WCAG thresholds
            if ratio < 3.0:
                level = "critical"
                required = 4.5
            elif ratio < 4.5:
                level = "major"
                required = 4.5
            elif ratio < 7.0:
                level = "minor"
                required = 7.0
            else:
                continue  # Passes AAA

            issues.append(ContrastIssue(
                level=level,
                element="text",
                foreground=c1["hex"],
                background=c2["hex"],
                ratio=ratio,
                required=required,
            ))

    return issues


# ── Luminance Statistics ──────────────────────────────────────────────


def _compute_luminance(img) -> LuminanceStats:
    """Compute mean brightness and standard deviation."""
    try:
        import numpy as np
        gray = np.array(img.convert("L"), dtype=np.float32)
        mean = float(np.mean(gray))
        std = float(np.std(gray))
        return LuminanceStats(
            mean=mean,
            std_dev=std,
            is_dark=mean < 85,
            is_light=mean > 170,
        )
    except ImportError:
        return LuminanceStats(mean=128, std_dev=0, is_dark=False, is_light=False)


# ── Main Audit Functions ──────────────────────────────────────────────


def audit_slide_image(image_path: Path | str, slide_index: int = 0) -> SlideAuditResult:
    """Run a full design audit on a single slide image.

    Args:
        image_path: Path to a PNG/JPG slide image.
        slide_index: Slide number for the result.

    Returns:
        SlideAuditResult with colors, contrast, complexity, and luminance.
    """
    img, w, h = _load_image(image_path)
    if img is None:
        return SlideAuditResult(
            slide_index=slide_index,
            warnings=["Cannot load image for analysis"],
        )

    # 1. Color palette
    palette = ColorPalette(colors=_extract_dominant_colors(img, k=5))

    # 2. Edge complexity
    complexity = _compute_edge_complexity(img)

    # 3. Contrast issues
    contrast_issues = _check_contrast_issues(palette)

    # 4. Luminance
    luminance = _compute_luminance(img)

    # 5. Overall score
    score = _compute_slide_score(palette, contrast_issues, complexity, luminance)

    # 6. Warnings
    warnings = _generate_warnings(contrast_issues, complexity, luminance)

    return SlideAuditResult(
        slide_index=slide_index,
        colors=palette,
        contrast_issues=contrast_issues,
        complexity=complexity,
        luminance=luminance,
        overall_score=score,
        warnings=warnings,
    )


def _compute_slide_score(
    palette: ColorPalette,
    contrast_issues: list[ContrastIssue],
    complexity: EdgeComplexity,
    luminance: LuminanceStats,
) -> float:
    """Compute overall design score (0-100)."""
    score = 70.0  # baseline

    # Color diversity penalty/award
    if len(palette.colors) < 3:
        score -= 10
    elif len(palette.colors) > 8:
        score -= 5  # too many colors

    # Contrast penalties
    critical = sum(1 for c in contrast_issues if c.level == "critical")
    major = sum(1 for c in contrast_issues if c.level == "major")
    score -= critical * 15
    score -= major * 8

    # Complexity
    if complexity.level == "simple":
        score += 5
    elif complexity.level == "complex":
        score -= 5

    # Luminance
    if luminance.is_dark:
        score -= 5  # very dark slides are hard to read
    if luminance.std_dev < 30:
        score -= 5  # very flat/boring

    return max(0, min(100, score))


def _generate_warnings(
    contrast_issues: list[ContrastIssue],
    complexity: EdgeComplexity,
    luminance: LuminanceStats,
) -> list[str]:
    """Generate human-readable warnings."""
    warnings = []

    for c in contrast_issues:
        if c.level == "critical":
            warnings.append(
                f"严重对比度问题: {c.foreground} 对 {c.background} "
                f"({c.ratio:.1f}:1, 需要 {c.required:.1f}:1)"
            )
        elif c.level == "major":
            warnings.append(
                f"中等对比度问题: {c.foreground} 对 {c.background} ({c.ratio:.1f}:1)"
            )

    if complexity.level == "complex":
        warnings.append("布局复杂度较高，建议简化")

    if luminance.is_dark:
        warnings.append("整体偏暗，建议提高亮度")

    if luminance.std_dev < 20:
        warnings.append("画面较单调，建议增加视觉层次")

    return warnings


# ── Template / Pipeline Integration ───────────────────────────────────


def audit_pptx_slides(slide_images: dict[int, Path]) -> DesignAudit:
    """Audit multiple slide images and produce aggregate report.

    Args:
        slide_images: Mapping of slide_index -> image file path.

    Returns:
        DesignAudit with per-slide results and aggregate score.
    """
    results = []
    total_score = 0.0
    critical = 0
    major = 0
    minor = 0
    all_warnings: list[str] = []

    for idx in sorted(slide_images.keys()):
        result = audit_slide_image(slide_images[idx], idx)
        results.append(result)
        total_score += result.overall_score
        critical += sum(1 for c in result.contrast_issues if c.level == "critical")
        major += sum(1 for c in result.contrast_issues if c.level == "major")
        minor += sum(1 for c in result.contrast_issues if c.level == "minor")
        all_warnings.extend(result.warnings)

    n = max(len(results), 1)
    aggregate = total_score / n

    # Aggregate recommendations
    recommendations = []
    if critical > 0:
        recommendations.append(f"修复 {critical} 个严重对比度问题")
    if major > 0:
        recommendations.append(f"改进 {major} 个中等对比度问题")
    avg_complexity = sum(
        0 if r.complexity.level == "simple" else
        1 if r.complexity.level == "moderate" else 2
        for r in results
    ) / n
    if avg_complexity > 1.3:
        recommendations.append("简化部分页面的布局复杂度")

    return DesignAudit(
        slide_results=results,
        aggregate_score=aggregate,
        critical_issues=critical,
        major_issues=major,
        minor_issues=minor,
        recommendations=recommendations,
    )


def audit_template(template_dir: Path | str) -> DesignAudit:
    """Audit a template's preview slides.

    Scans template_dir/preview/slide_*.png and runs design analysis.

    Args:
        template_dir: Path to the template directory (e.g. templates/tech/modern).

    Returns:
        DesignAudit with per-slide results.
    """
    template_dir = Path(template_dir)
    preview_dir = template_dir / "preview"

    slide_images: dict[int, Path] = {}
    if preview_dir.exists():
        for png in sorted(preview_dir.glob("slide_*.png")):
            stem = png.stem
            try:
                idx = int(stem.split("_")[1])
                slide_images[idx] = png
            except (IndexError, ValueError):
                continue

    if not slide_images:
        logger.warning("No preview images found in %s", preview_dir)
        return DesignAudit(
            recommendations=["No preview images available for design audit."],
        )

    return audit_pptx_slides(slide_images)
