from __future__ import annotations


def slide_contents_to_batch_config(slide_contents: dict) -> dict:
    slides = []
    for slide in slide_contents.get("slides", []):
        prompt = None
        output_name = f"slide_{slide['slide_index']:02d}.png"
        reference_image = None
        fallback = "placeholder"
        for zone in slide.get("zones", []):
            if zone.get("type") == "image":
                prompt = zone.get("image_prompt") or "Professional supporting visual for this slide."
                reference_image = zone.get("image_ref")
                fallback = "user_image" if reference_image else "placeholder"
        slides.append(
            {
                "index": slide["slide_index"],
                "mode": "region",
                "prompt": prompt or "Professional supporting visual for this slide.",
                "aspect_ratio": "16:9",
                "resolution": "1K",
                "reference_image": reference_image,
                "output_name": output_name,
                "fallback": fallback,
            }
        )
    return {"slides": slides}
