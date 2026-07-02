from __future__ import annotations

from ppt_agent.models.dispatch import DispatchRequest
from ppt_agent.models.schema_loader import load_schema, validate_required
from ppt_agent.retrieval.config import load_kb_config


def test_dispatch_request_contract_required_keys() -> None:
    payload = DispatchRequest("job", "document_analysis", "document_analysis", ["input"]).to_dict()
    validate_required(load_schema("dispatch-request.schema.json"), payload, "dispatch_request")


def test_kb_config_contract_required_keys() -> None:
    payload = load_kb_config(__import__("pathlib").Path("config/knowledge-bases/templates.yml"))
    validate_required(load_schema("knowledge-base-config.schema.json"), payload, "kb_config")
