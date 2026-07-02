from __future__ import annotations

from ppt_agent.models.outline import OutlineSlide, PresentationOutline
from ppt_agent.models.schema_loader import load_schema, validate_required


def test_outline_contract_minimal() -> None:
    payload = PresentationOutline(
        {"project_name": "Project", "domain": "general", "audience": "stakeholders", "tone": "professional", "total_slides": 1},
        [OutlineSlide(0, "cover", "Project", "Introduce", "placeholder")],
    ).to_dict()
    validate_required(load_schema("outline.schema.json"), payload, "outline")
