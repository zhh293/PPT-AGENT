from __future__ import annotations


def default_design_plan(slide_count: int) -> dict:
    return {
        "theme_profile": {
            "theme_id": "fallback.professional",
            "color_tokens": {
                "primary": "#1F4E79",
                "secondary": "#70AD47",
                "accent": "#F4B183",
                "background": "#FFFFFF",
                "text": "#1F2933",
                "muted": "#6B7280",
            },
            "typography_tokens": {"title_font": "Aptos Display", "body_font": "Aptos", "title_scale": 1.0, "body_scale": 1.0},
            "spacing_tokens": {"page_margin": 0.08, "block_gap": 0.03, "card_padding": 0.02},
            "shape_tokens": {"border_radius": 0.02, "stroke": "light"},
            "image_treatment": {"crop": "contain", "tone": "natural"},
            "chart_style": {"palette": ["#1F4E79", "#70AD47", "#F4B183"]},
        },
        "slides": [
            {
                "slide_index": i,
                "layout_id": "cover.hero" if i == 0 else "fallback.basic",
                "visual_density": "low" if i in {0, slide_count - 1} else "medium",
                "block_plan": [{"block_type": "editable_text", "purpose": "communicate slide message", "priority": "primary"}],
                "visual_strategy": "placeholder",
                "text_budget": {"max_title_chars": 70, "max_bullets": 5, "max_lines_per_block": 8},
                "design_constraints": ["preserve approved text as editable PPT text"],
                "design_warnings": [],
            }
            for i in range(slide_count)
        ],
        "global_style_notes": ["Fallback layout prioritizes readable editable text."],
        "design_risks": [],
    }
