from __future__ import annotations

import json

from ppt_agent.cli import _finalize_job_status
from ppt_agent.llm.client import LLMClient
from ppt_agent.llm.config import ProviderConfig
from ppt_agent.llm.messages import LLMResult
from ppt_agent.llm.providers.base import BaseLLMProvider, ProviderCapabilities
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.runtime.agent_loop import AgentLoop, StopReason, ToolCall, ToolResult
from ppt_agent.skills.adapters.gptimage2 import slide_contents_to_batch_config
from ppt_agent.workers.image_generator import check_generated_images


class _PaymentRequiredProvider(BaseLLMProvider):
    def __init__(self) -> None:
        super().__init__(
            ProviderConfig(name="payment", protocol="fake", text_model="fake")
        )
        self.calls = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_strict_tools=True)

    def generate(self, messages, **kwargs) -> LLMResult:
        self.calls += 1
        return LLMResult(
            provider=self.name,
            model="fake",
            error="Error code: 402 - Insufficient Balance",
        )


def test_payment_required_opens_circuit_and_skips_strict_retries() -> None:
    provider = _PaymentRequiredProvider()
    client = LLMClient.from_provider(provider)
    schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }

    first = client.generate_json(
        prompt="Generate.",
        json_schema=schema,
        schema_name="result",
        fallback={"value": "fallback"},
        phase="document_analysis",
    )
    second = client.generate_json(
        prompt="Generate again.",
        json_schema=schema,
        schema_name="result",
        fallback={"value": "fallback"},
        phase="outline_generation",
    )

    assert first == second == {"value": "fallback"}
    assert provider.calls == 1
    assert client.circuit_open
    assert "402" in client.terminal_error


def test_agent_loop_stops_after_first_terminal_provider_error() -> None:
    class _Agent(AgentLoop):
        def build_system_prompt(self) -> str:
            return "test"

        def get_available_tools(self) -> list[dict]:
            return []

        def execute_tool(self, tool_call: ToolCall) -> ToolResult:
            return ToolResult(call_id=tool_call.call_id, output={}, success=True)

        def parse_llm_response(self, result: LLMResult):
            return result.text, [], False

    provider = _PaymentRequiredProvider()
    agent = _Agent(
        agent_id="terminal",
        llm_client=LLMClient.from_provider(provider),
        max_turns=5,
    )

    result = agent.run("test")

    assert provider.calls == 1
    assert len(result.turns) == 1
    assert result.stop_reason == StopReason.ERROR_BUDGET


def test_failed_validation_cannot_be_published_as_clean_completion(tmp_path) -> None:
    job = tmp_path / "job"
    job.mkdir()
    (job / "job.json").write_text(
        json.dumps({"job_id": "job", "status": "running"}),
        encoding="utf-8",
    )
    (job / "validation_report.json").write_text(
        json.dumps({"job_id": "job", "status": "failed", "summary": "QA failed"}),
        encoding="utf-8",
    )

    _finalize_job_status(job)

    meta = json.loads((job / "job.json").read_text(encoding="utf-8"))
    assert meta["status"] == "completed_with_fallbacks"
    assert meta["quality_status"] == "failed"
    assert meta["quality_summary"] == "QA failed"


def test_image_report_counts_presentation_slides_not_visual_jobs(tmp_path) -> None:
    workspace = JobWorkspace(tmp_path / "job")
    workspace.ensure()
    workspace.artifact_path("slide_contents").write_text(
        json.dumps({"slides": [{"slide_index": 0}, {"slide_index": 1}]}),
        encoding="utf-8",
    )
    config = {
        "slides": [
            {"index": 0, "output_name": "slide-000-a.png"},
            {"index": 0, "output_name": "slide-000-b.png"},
            {"index": 1, "output_name": "slide-001-a.png"},
        ]
    }

    report = check_generated_images(workspace, config)

    assert report["total_slides"] == 2
    assert report["fallback"] == 2
    assert len(report["slide_results"]) == 2
    assert [item["slide_index"] for item in report["slide_results"]] == [0, 1]


def test_preserved_template_images_do_not_become_generation_jobs() -> None:
    config = slide_contents_to_batch_config(
        {
            "assembly_policy": {"mode": "text_replace_only"},
            "slides": [
                {
                    "slide_index": 0,
                    "zones": [
                        {
                            "zone_id": "image-1",
                            "type": "image",
                            "action": "preserve",
                            "source": "template",
                            "image_prompt": "A generic placeholder prompt",
                        }
                    ],
                }
            ],
        },
        mode="all",
    )

    assert config["slides"] == []
