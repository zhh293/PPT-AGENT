"""JSON Schema definitions for structured output mode.

When passed to ``generate_json(json_schema=..., schema_name=...)``,
the provider uses ``response_format: json_schema`` with ``strict: true``
to guarantee valid JSON at the API level — eliminating local repair.

Each schema is a standard JSON Schema (2020-12) dict.  Only the fields
that the LLM MUST output are marked required; optional fields (e.g.
image_inventory which is populated from file scanning) are omitted.

For *content_mapping*, structured output is NOT used because zone_ids
are dynamic (template-dependent) — it falls back to ``json_object`` mode.
"""

# ── document_analysis → source_summary ──────────────────────────────

SOURCE_SUMMARY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "project_name":        {"type": "string"},
        "domain":              {"type": "string"},
        "target_audience":     {"type": "string"},
        "tone":                {"type": "string"},
        "value_proposition":   {"type": "string"},
        "product_capabilities": {
            "type": "array",
            "items": {"type": "string"},
        },
        "evidence_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "evidence_id": {"type": "string"},
                    "summary":     {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                    "confidence":  {"type": "number"},
                },
                "required": ["evidence_id", "summary", "source_refs", "confidence"],
                "additionalProperties": False,
            },
        },
        "core_pain_points": {
            "type": "array",
            "items": {"type": "string"},
        },
        "unsupported_claims": {
            "type": "array",
            "items": {"type": "string"},
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
        },
        "confidence": {"type": "number"},
    },
    "required": [
        "project_name", "domain", "target_audience", "tone",
        "value_proposition", "product_capabilities", "evidence_items",
        "core_pain_points", "unsupported_claims", "warnings", "confidence",
    ],
    "additionalProperties": False,
}

# ── outline_generation → outline ────────────────────────────────────

OUTLINE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "meta": {
            "type": "object",
            "properties": {
                "project_name":     {"type": "string"},
                "domain":           {"type": "string"},
                "audience":         {"type": "string"},
                "tone":             {"type": "string"},
                "total_slides":     {"type": "integer"},
                "assumptions":      {"type": "array", "items": {"type": "string"}},
                "needs_user_review": {"type": "boolean"},
            },
            "required": [
                "project_name", "domain", "audience", "tone",
                "total_slides", "assumptions", "needs_user_review",
            ],
            "additionalProperties": False,
        },
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "slide_index": {"type": "integer"},
                    "type":        {"type": "string"},
                    "title":       {"type": "string"},
                    "purpose":     {"type": "string"},
                    "bullets":     {"type": "array", "items": {"type": "string"}},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                    "image_needs": {"type": "string"},
                    "priority":    {"type": "string"},
                },
                "required": [
                    "slide_index", "type", "title", "purpose",
                    "bullets", "source_refs", "image_needs", "priority",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["meta", "slides"],
    "additionalProperties": False,
}

# ── design_planning → slide_design_plan ─────────────────────────────

DESIGN_PLAN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "theme_profile": {
            "type": "object",
            "properties": {
                "theme_id":          {"type": "string"},
                "color_tokens":      {
                    "type": "object",
                    "properties": {
                        "primary":    {"type": "string"},
                        "secondary":  {"type": "string"},
                        "accent":     {"type": "string"},
                        "background": {"type": "string"},
                        "text":       {"type": "string"},
                        "muted":      {"type": "string"},
                    },
                    "required": ["primary", "secondary", "accent", "background", "text", "muted"],
                    "additionalProperties": False,
                },
                "typography_tokens": {
                    "type": "object",
                    "properties": {
                        "title_font": {"type": "string"},
                        "body_font":  {"type": "string"},
                        "title_scale": {"type": "number"},
                        "body_scale":  {"type": "number"},
                    },
                    "required": ["title_font", "body_font", "title_scale", "body_scale"],
                    "additionalProperties": False,
                },
                "spacing_tokens": {
                    "type": "object",
                    "properties": {
                        "page_margin": {"type": "number"},
                        "block_gap":   {"type": "number"},
                        "card_padding": {"type": "number"},
                    },
                    "required": ["page_margin", "block_gap", "card_padding"],
                    "additionalProperties": False,
                },
                "shape_tokens": {
                    "type": "object",
                    "properties": {
                        "border_radius": {"type": "number"},
                        "stroke":        {"type": "string"},
                    },
                    "required": ["border_radius", "stroke"],
                    "additionalProperties": False,
                },
                "image_treatment": {
                    "type": "object",
                    "properties": {
                        "crop": {"type": "string"},
                        "tone": {"type": "string"},
                    },
                    "required": ["crop", "tone"],
                    "additionalProperties": False,
                },
                "chart_style": {
                    "type": "object",
                    "properties": {
                        "palette": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["palette"],
                    "additionalProperties": False,
                },
            },
            "required": ["theme_id", "color_tokens", "typography_tokens",
                         "spacing_tokens", "shape_tokens", "image_treatment",
                         "chart_style"],
            "additionalProperties": False,
        },
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "slide_index":         {"type": "integer"},
                    "visual_density":      {"type": "string"},
                    "block_plan":          {"type": "array", "items": {
                        "type": "object",
                        "properties": {
                            "block_type": {"type": "string"},
                            "purpose":    {"type": "string"},
                            "priority":   {"type": "string"},
                        },
                        "additionalProperties": False,
                    }},
                    "visual_strategy":     {"type": "string"},
                    "text_budget":         {
                        "type": "object",
                        "properties": {
                            "max_title_chars":     {"type": "integer"},
                            "max_bullets":         {"type": "integer"},
                            "max_lines_per_block": {"type": "integer"},
                        },
                        "required": ["max_title_chars", "max_bullets", "max_lines_per_block"],
                        "additionalProperties": False,
                    },
                    "design_constraints": {"type": "array", "items": {"type": "string"}},
                    "design_warnings":    {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "slide_index", "visual_density",
                    "block_plan", "visual_strategy", "text_budget",
                    "design_constraints", "design_warnings",
                ],
                "additionalProperties": False,
            },
        },
        "global_style_notes": {"type": "array", "items": {"type": "string"}},
        "design_risks":       {"type": "array", "items": {"type": "string"}},
    },
    "required": ["theme_profile", "slides", "global_style_notes", "design_risks"],
    "additionalProperties": False,
}

# ── verification → validation_report ────────────────────────────────

VERIFICATION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "content_completeness": {
            "type": "object",
            "properties": {
                "score":  {"type": "integer"},
                "issues": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["score", "issues"],
            "additionalProperties": False,
        },
        "visual_consistency": {
            "type": "object",
            "properties": {
                "score":  {"type": "integer"},
                "issues": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["score", "issues"],
            "additionalProperties": False,
        },
        "text_accuracy": {
            "type": "object",
            "properties": {
                "score":  {"type": "integer"},
                "issues": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["score", "issues"],
            "additionalProperties": False,
        },
        "overall_score": {"type": "integer"},
        "summary":       {"type": "string"},
    },
    "required": [
        "content_completeness", "visual_consistency",
        "text_accuracy", "overall_score", "summary",
    ],
    "additionalProperties": False,
}

# ── content_mapping → slide_contents ────────────────────────────────
# Zone-level fields are fully defined so strict tool calling can
# guarantee valid JSON even with dynamic zone_ids from templates.
# Fields that may be null (formatting, content, image_ref, etc.) use
# anyOf to accept both the value type and null.

SLIDE_CONTENTS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "template_id": {"type": "string"},
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "slide_index":    {"type": "integer"},
                    "layout":         {"type": "string"},
                    "layout_id":      {"type": "string"},
                    "visual_density": {"type": "string"},
                    "zones": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "zone_id":          {"type": "string"},
                                "type":             {"type": "string"},
                                "position":         {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                                "editable":         {"type": "boolean"},
                                "content":          {"type": "array", "items": {"type": "string"}},
                                "source":           {"type": "string"},
                                "fit_status":       {"type": "string"},
                                "placement_reason": {"type": "string"},
                            },
                            "required": [
                                "zone_id", "type", "position", "editable",
                                "content", "source", "fit_status", "placement_reason",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["slide_index", "layout", "layout_id", "visual_density", "zones"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["template_id", "slides"],
    "additionalProperties": False,
}
