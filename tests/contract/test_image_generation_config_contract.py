from __future__ import annotations

from ppt_agent.models.schema_loader import load_schema, validate_required
from ppt_agent.skills.adapters.gptimage2 import slide_contents_to_batch_config


def test_image_generation_config_contract() -> None:
    config = slide_contents_to_batch_config(
        {"slides": [{"slide_index": 0, "zones": [{"type": "image", "image_prompt": "dashboard visual"}]}]}
    )
    validate_required(load_schema("image-generation-config.schema.json"), config, "image_generation_config")
