from __future__ import annotations

from ppt_agent.models.schema_loader import load_schema, validate_required
from ppt_agent.models.template_meta import default_template_meta


def test_template_meta_contract_required_keys() -> None:
    validate_required(load_schema("template-meta.schema.json"), default_template_meta(2), "template_meta")
