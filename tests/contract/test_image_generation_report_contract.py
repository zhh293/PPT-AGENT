from __future__ import annotations

from ppt_agent.models.image_generation import fallback_image_report
from ppt_agent.models.schema_loader import load_schema, validate_required


def test_image_generation_report_contract_minimal() -> None:
    validate_required(load_schema("image-generation-report.schema.json"), fallback_image_report("job", 1), "image_report")
