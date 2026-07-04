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


def slide_contents_to_batch_config(slide_contents: dict) -> dict:
    """Convert slide_contents into GPTImage2 batch-generate config.

    Each slide entry in the config has:
        - index: slide number
        - mode: "full_page" (always, for background generation)
        - prompt: text content + visual instructions
        - aspect_ratio: "16:9"
        - resolution: "1K"
        - reference_image: template slide image path (for img2img)
        - output_name: slide_XX.png
    """
    slides = []
    for slide in slide_contents.get("slides", []):
        idx = slide["slide_index"]
        output_name = f"slide_{idx:02d}.png"

        # Build prompt from slide content
        prompt = _build_slide_prompt(slide)

        # Get template image for img2img reference
        reference_image = slide.get("template_image")

        slides.append({
            "index": idx,
            "mode": "full_page",
            "prompt": prompt,
            "aspect_ratio": "16:9",
            "resolution": "1K",
            "reference_image": reference_image,
            "output_name": output_name,
            "fallback": "placeholder" if not reference_image else "text2img",
        })

    return {"slides": slides}


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
        # img2img mode: preserve template visual style
        parts.append(
            "Generate a professional presentation slide background. "
            "Maintain the visual style, color scheme, and layout structure "
            "of the reference image. Replace text content with the provided "
            "title and content. The result should be a polished, full-page "
            "16:9 slide image suitable as a presentation background."
        )
    else:
        # text2img mode: generate from scratch
        parts.append(
            f"Generate a professional presentation slide background with "
            f"{density} visual density. Style: clean, modern, corporate. "
            f"Layout type: {layout}. The image should be a full 16:9 slide "
            f"with appropriate visual elements but clear areas for text overlay. "
            f"Do NOT render actual text in the image — text will be added separately."
        )

    return " | ".join(parts)
