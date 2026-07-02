from __future__ import annotations

DEFAULT_ZONES = [
    {"zone_id": "title", "type": "title", "position": [0.08, 0.08, 0.84, 0.16], "font_size": 34},
    {"zone_id": "bullets", "type": "bullets", "position": [0.10, 0.28, 0.52, 0.54], "font_size": 20},
    {"zone_id": "image", "type": "image", "position": [0.68, 0.30, 0.24, 0.40], "font_size": 14},
]


def default_template_meta(slide_count: int = 8) -> dict:
    return {
        "template_id": "fallback.default",
        "domain_tags": ["general", "business"],
        "audience_tags": ["stakeholders", "reviewers"],
        "tone_tags": ["professional", "clear"],
        "color_scheme": {
            "primary": "#1F4E79",
            "secondary": "#70AD47",
            "accent": "#F4B183",
            "background": "#FFFFFF",
        },
        "style": "Clean professional fallback template with editable text zones.",
        "slide_count": slide_count,
        "preview_paths": [],
        "retrieval_text": "general business project report professional clean",
        "slides": [
            {
                "index": i,
                "layout": "fallback.basic" if i else "cover.hero",
                "visual_density": "medium",
                "supports_generated_background": False,
                "zones": DEFAULT_ZONES,
            }
            for i in range(slide_count)
        ],
    }
