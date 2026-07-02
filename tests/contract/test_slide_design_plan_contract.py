from __future__ import annotations

from ppt_agent.models.design_plan import default_design_plan
from ppt_agent.models.schema_loader import load_schema, validate_required


def test_slide_design_plan_contract_minimal() -> None:
    validate_required(load_schema("slide-design-plan.schema.json"), default_design_plan(1), "slide_design_plan")
