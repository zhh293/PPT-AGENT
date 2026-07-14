"""GPTImage2 adapter — convert slide_contents into batch generation config.

The new flow generates FULL-PAGE background images via img2img:
- Each slide's template image serves as the reference (--reference)
- The prompt includes the slide's text content from the outline
- Output is a 16:9 full-page image to be used as slide background
- Text is overlaid separately by ppt_writer (not baked into the image)

When no template image is available (fallback template), falls back to
text-to-image mode with a descriptive prompt.
"""

from __future__ import annotations


def _evaluate_template_background(image_path: str | None) -> tuple[float, bool]:
    """Evaluate if a template background image is good enough to keep.

    Returns (quality_score, should_generate).

    quality_score: 0.0-1.0, higher = better template background.
    should_generate: True if the template background is NOT good enough.
    """
    if not image_path:
        return 0.0, True  # No template image → must generate

    from pathlib import Path
    p = Path(image_path)
    if not p.exists():
        return 0.0, True

    try:
        from PIL import Image
        import numpy as np
        img = Image.open(p).convert("RGB")
        arr = np.array(img, dtype=np.float32)

        h, w = arr.shape[:2]
        if h < 100 or w < 100:
            return 0.0, True  # Too small to evaluate

        # ── Edge density (Sobel) ──
        gray = np.mean(arr, axis=2)
        if h >= 3 and w >= 3:
            gx = np.abs(gray[1:-1, 2:] - gray[1:-1, :-2])
            gy = np.abs(gray[2:, 1:-1] - gray[:-2, 1:-1])
            edge_density = float(np.mean((gx + gy) / 2 > 20))
        else:
            edge_density = 0.0

        # ── Color variance ──
        color_std = float(np.std(arr, axis=(0, 1)).mean())

        # ── Brightness ──
        brightness = float(gray.mean())

        # ── Scoring ──
        # Edge: 0.02-0.15 is good (some texture but not chaotic)
        edge_score = 1.0 if 0.02 <= edge_density <= 0.20 else (
            edge_density / 0.02 if edge_density < 0.02 else
            max(0, 1.0 - (edge_density - 0.20) / 0.20)
        )

        # Color: >20 std is visually interesting, <5 is solid color
        color_score = min(1.0, color_std / 20.0)

        # Brightness: 60-200 is readable, extremes are bad
        brightness_score = 1.0 if 60 <= brightness <= 200 else (
            brightness / 60.0 if brightness < 60 else
            max(0, 1.0 - (brightness - 200) / 55.0)
        )

        quality = edge_score * 0.4 + color_score * 0.4 + brightness_score * 0.2

        # Threshold: below 0.4 → needs generation
        should_generate = quality < 0.4

        return round(quality, 3), should_generate

    except ImportError:
        return 0.5, False  # Can't evaluate, assume it's fine
    except Exception:
        return 0.5, False


def slide_contents_to_batch_config(
    slide_contents: dict,
    mode: str = "none",
    template_root: str | None = None,
) -> dict:
    """Convert slide_contents into GPTImage2 batch-generate config.

    Parameters
    ----------
    slide_contents
        Slide data from the outline phase.
    mode
        Which slides to generate AI backgrounds for:

        - ``"key"`` (default) — cover + section/chapter dividers only
        - ``"all"`` — every slide gets an AI background
        - ``"cover-only"`` — just the first slide
        - ``"none"`` — skip generation entirely

    Each slide entry in the config has:
        - index: slide number
        - prompt: text content + visual instructions
        - aspect_ratio: "16:9"
        - resolution: "1K"
        - reference_image: template slide image path (for img2img)
        - output_name: slide_XX.png
    """
    slides = slide_contents.get("slides", [])

    # Determine which slides to generate
    if mode == "none":
        return {"slides": []}

    kept_count = 0
    generated_count = 0
    to_generate: list[dict] = []
    for slide in slides:
        idx = slide["slide_index"]
        layout = slide.get("layout", "")

        # ── Mode filter ──
        if mode == "cover-only" and idx != 0:
            continue
        elif mode == "key":
            is_cover = (idx == 0)
            is_section = any(
                tag in str(layout).lower()
                for tag in ("cover", "section", "chapter", "divider", "title")
            )
            if not (is_cover or is_section):
                continue

        # ── Quality check: keep template background if it looks good ──
        reference_image = slide.get("template_image")  # Already absolute or None
        quality, should_generate = _evaluate_template_background(reference_image)

        if not should_generate:
            kept_count += 1
            continue  # Template background is good enough → skip generation

        generated_count += 1
        output_name = f"slide-{idx:03d}.png"
        prompt = _build_slide_prompt(slide)

        to_generate.append({
            "index": idx,
            "mode": "full_page",
            "prompt": prompt,
            "aspect_ratio": "16:9",
            "resolution": "1K",
            "reference_image": reference_image,
            "output_name": output_name,
            "fallback": "placeholder" if not reference_image else "text2img",
            "template_quality_score": quality,
        })

    # ── Inline image zones: generate content images (not backgrounds) ──
    inline_count = 0
    for slide in slides:
        idx = slide["slide_index"]
        for zi, zone in enumerate(slide.get("zones", [])):
            if zone.get("type") != "image":
                continue
            image_prompt = zone.get("image_prompt")
            image_ref = zone.get("image_ref")
            # Skip if: no prompt, or user provided an image, or prompt is empty
            if not image_prompt or not str(image_prompt).strip():
                continue
            if image_ref:
                continue  # User-uploaded image → don't generate

            inline_count += 1
            zone_id = zone.get("zone_id", f"image_{idx}_{zi}")
            # GPTImage2 script uses output_name directly; for multiple inline
            # images per slide, append a short index suffix to keep names unique.
            suffix = f"-{inline_count}" if inline_count > 1 else ""
            output_name = f"slide-{idx:03d}{suffix}.png"

            # Build a focused prompt for the inline image
            slide_context = _build_slide_title(slide)
            inline_prompt = (
                f"Content image for slide about '{slide_context}'. "
                f"{image_prompt}. "
                f"Clean professional style, no text in the image."
            )

            to_generate.append({
                "index": idx,
                "mode": "content_image",
                "zone_id": zone_id,
                "prompt": inline_prompt,
                "aspect_ratio": "4:3",
                "resolution": "1K",
                "reference_image": None,
                "output_name": output_name,
                "fallback": "placeholder",
            })

    import logging
    logger = logging.getLogger(__name__)
    logger.info(
        "Background: %d kept, %d generated. Inline images: %d to generate (mode=%s)",
        kept_count, generated_count, inline_count, mode,
    )

    return {
        "slides": to_generate,
        "_inline_map": {  # zone_id → output_name for assembler
            entry["zone_id"]: entry["output_name"]
            for entry in to_generate if entry.get("mode") == "content_image"
        },
    }


def _build_slide_title(slide: dict) -> str:
    """Extract a short title from a slide for context in image prompts."""
    for zone in slide.get("zones", []):
        if zone.get("type") == "title" and zone.get("content"):
            return str(zone["content"])[:60]
    return f"slide {slide.get('slide_index', 0)}"


def _build_slide_prompt(slide: dict) -> str:
    """Build a generation prompt from slide content.

    Combines the slide's text content into a prompt that tells the AI
    to generate a full-page background image that incorporates the content
    while maintaining the template's visual style.
    """
    parts: list[str] = []

    # Extract text content from zones
    title = ""
    bullets: list[str] = []
    for zone in slide.get("zones", []):
        zone_type = zone.get("type", "")
        content = zone.get("content")

        if zone_type == "title" and content:
            title = str(content)
        elif zone_type in ("bullets", "body") and content:
            if isinstance(content, list):
                bullets.extend(str(b) for b in content)
            elif content:
                bullets.append(str(content))

    # Build descriptive prompt
    if title:
        parts.append(f"Title: {title}")
    if bullets:
        bullet_text = "; ".join(bullets[:5])  # Limit to avoid prompt overflow
        parts.append(f"Content: {bullet_text}")

    # Layout and style instructions
    layout = slide.get("layout", "content.text")
    density = slide.get("visual_density", "medium")

    if slide.get("template_image"):
        # img2img mode: preserve template visual style, NO text
        parts.append(
            "Generate a professional presentation slide BACKGROUND. "
            "Maintain the visual style, color scheme, and decorative elements "
            "(shapes, lines, gradients, icons) of the reference image. "
            "IMPORTANT: Do NOT include ANY text, letters, words, numbers "
            "or typography in the image. Text will be overlaid separately "
            "as editable PPT text boxes. Focus purely on the visual "
            "atmosphere — textures, geometric patterns, subtle gradients, "
            "and abstract decorative shapes. The result should be a polished "
            "16:9 slide background ready for text overlay."
        )
    else:
        # text2img mode: generate from scratch, NO text
        parts.append(
            f"Generate a professional presentation slide BACKGROUND with "
            f"{density} visual density. Style: clean, modern, corporate. "
            f"Layout type: {layout}. "
            f"IMPORTANT: Do NOT include ANY text, letters, words, numbers "
            f"or typography. Text will be added separately as editable "
            f"PPT text boxes. Focus purely on visual atmosphere — gradients, "
            f"geometric shapes, subtle patterns, and decorative elements. "
            f"The image should be a full 16:9 slide background with clear "
            f"areas for text overlay."
        )

    return " | ".join(parts)
