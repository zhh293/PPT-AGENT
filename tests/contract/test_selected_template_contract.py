from __future__ import annotations

from ppt_agent.models.schema_loader import load_schema, validate_required
from ppt_agent.models.selected_template import fallback_selection


def test_selected_template_contract_minimal() -> None:
    validate_required(load_schema("selected-template.schema.json"), fallback_selection(), "selected_template")
