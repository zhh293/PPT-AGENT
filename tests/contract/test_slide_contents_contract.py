from __future__ import annotations

from ppt_agent.models.schema_loader import load_schema, validate_required


def test_slide_contents_contract_minimal() -> None:
    payload = {
        "template_id": "fallback.default",
        "slides": [
            {
                "slide_index": 0,
                "layout": "cover.hero",
                "zones": [{"zone_id": "title", "type": "title", "position": [0, 0, 1, 0.2], "editable": True, "content": "Project"}],
            }
        ],
    }
    validate_required(load_schema("slide-contents.schema.json"), payload, "slide_contents")
