from __future__ import annotations

from ppt_agent.models.schema_loader import load_schema, validate_required
from ppt_agent.models.selected_template import fallback_selection
from ppt_agent.models.template_meta import default_template_meta


def test_template_meta_and_ranking_contracts() -> None:
    validate_required(load_schema("template-meta.schema.json"), default_template_meta(3), "template_meta")
    validate_required(load_schema("selected-template.schema.json"), fallback_selection(), "selected_template")
