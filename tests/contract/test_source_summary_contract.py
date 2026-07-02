from __future__ import annotations

from ppt_agent.models.schema_loader import load_schema, validate_required


def test_source_summary_contract_minimal() -> None:
    payload = {
        "project_name": "Project",
        "domain": "general",
        "target_audience": "stakeholders",
        "tone": "professional",
        "value_proposition": "",
        "product_capabilities": [],
        "evidence_items": [],
        "image_inventory": [],
        "unsupported_claims": [],
        "confidence": 0.5,
    }
    validate_required(load_schema("source-summary.schema.json"), payload, "source_summary")
