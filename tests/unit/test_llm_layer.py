"""Tests for the LLM abstraction layer.

Tests config loading, message conversion, JSON repair, fake provider,
and LLMClient integration with fake provider.
"""

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ppt_agent.llm.config import ModelConfig, ProviderConfig, load_model_config, _default_config
from ppt_agent.llm.messages import LLMMessage, ContentBlock, LLMResult
from ppt_agent.llm.json_repair import extract_json_object, repair_json
from ppt_agent.llm.response_parser import parse_json_response
from ppt_agent.llm.audit import log_model_call
from ppt_agent.llm.providers.fake import FakeProvider
from ppt_agent.llm.providers.openai_compatible import OpenAICompatibleProvider
from ppt_agent.llm.providers.base import BaseLLMProvider, ProviderCapabilities
from ppt_agent.llm.client import LLMClient, _create_provider
from ppt_agent.llm.schemas import (
    build_content_mapping_schema,
    normalize_content_mapping_response,
)


class TestConfig:
    def test_default_config_returns_fake(self):
        config = _default_config()
        assert config.default_provider == "fake"
        assert "fake" in config.providers

    def test_load_config_missing_file_returns_default(self):
        config = load_model_config("/nonexistent/path.yml")
        assert config.default_provider == "fake"

    def test_load_config_from_yaml(self):
        yaml_content = """
default_provider: anthropic
providers:
  anthropic:
    protocol: anthropic_messages
    api_key_env: ANTHROPIC_API_KEY
    text_model: claude-sonnet-4-20250514
    temperature: 0.3
    max_tokens: 8000
  fake:
    protocol: fake
    text_model: fake
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            config = load_model_config(f.name)

        os.unlink(f.name)
        assert config.default_provider == "anthropic"
        assert "anthropic" in config.providers
        assert config.providers["anthropic"].protocol == "anthropic_messages"
        assert config.providers["anthropic"].text_model == "claude-sonnet-4-20250514"
        assert config.providers["anthropic"].temperature == 0.3

    def test_get_provider_raises_on_unknown(self):
        config = _default_config()
        with pytest.raises(ValueError, match="not found"):
            config.get_provider("nonexistent")

    def test_provider_api_key_from_env(self):
        provider = ProviderConfig(name="test", protocol="fake", api_key_env="TEST_KEY_12345")
        os.environ["TEST_KEY_12345"] = "secret"
        assert provider.get_api_key() == "secret"
        del os.environ["TEST_KEY_12345"]

    def test_provider_base_url_from_endpoint(self):
        provider = ProviderConfig(name="test", protocol="fake", endpoint="http://localhost:8000")
        assert provider.get_base_url() == "http://localhost:8000"


class TestMessages:
    def test_system_message(self):
        msg = LLMMessage.system("You are helpful.")
        assert msg.role == "system"
        assert msg.text() == "You are helpful."

    def test_user_message(self):
        msg = LLMMessage.user("Hello")
        assert msg.role == "user"
        assert msg.content[0].type == "text"

    def test_user_with_images(self):
        msg = LLMMessage.user_with_images("Look at this", ["/path/img.png"])
        assert len(msg.content) == 2
        assert msg.content[0].type == "text"
        assert msg.content[1].type == "image"
        assert msg.content[1].path == "/path/img.png"

    def test_content_block_text(self):
        block = ContentBlock.text_block("hello")
        assert block.type == "text"
        assert block.text == "hello"

    def test_content_block_json(self):
        block = ContentBlock.json_block({"key": "value"})
        assert block.type == "json"
        assert '"key"' in block.text

    def test_llm_result_success(self):
        result = LLMResult(text="output", provider="test", model="test-model")
        assert result.success is True
        assert result.error is None

    def test_llm_result_failure(self):
        result = LLMResult(error="timeout", provider="test", model="test-model")
        assert result.success is False


class TestJsonRepair:
    def test_extract_from_code_fence(self):
        text = '```json\n{"key": "value"}\n```'
        result = extract_json_object(text)
        assert result == '{"key": "value"}'

    def test_extract_from_raw_text(self):
        text = 'Some explanation: {"key": "value"} end.'
        result = extract_json_object(text)
        assert json.loads(result) == {"key": "value"}

    def test_extract_no_json(self):
        assert extract_json_object("no json here") is None

    def test_repair_valid_json(self):
        data, warnings = repair_json('{"key": "value"}')
        assert data == {"key": "value"}
        assert not warnings

    def test_repair_trailing_comma(self):
        data, warnings = repair_json('{"key": "value",}')
        assert data is not None
        assert data["key"] == "value"

    def test_repair_missing_brace(self):
        data, warnings = repair_json('{"key": "value"')
        assert data is not None

    def test_repair_total_garbage(self):
        data, warnings = repair_json("this is not json at all")
        assert data is None
        assert len(warnings) > 0  # Should report some warning about failure


class TestResponseParser:
    def test_parse_success(self):
        result = LLMResult(text='{"status": "ok"}', provider="test", model="test")
        data, warnings = parse_json_response(result)
        assert data == {"status": "ok"}

    def test_parse_error_result(self):
        result = LLMResult(error="timeout", provider="test", model="test")
        data, warnings = parse_json_response(result)
        assert data is None
        assert any("failed" in w.lower() for w in warnings)

    def test_parse_with_pre_populated_json(self):
        result = LLMResult(json_data={"pre": "populated"}, provider="test", model="test")
        data, warnings = parse_json_response(result)
        assert data == {"pre": "populated"}

    def test_schema_validation_warns_missing(self):
        result = LLMResult(text='{"a": 1}', provider="test", model="test")
        schema = {"required": ["a", "b"]}
        data, warnings = parse_json_response(result, schema)
        assert data is None
        assert any("b" in w for w in warnings)

    def test_schema_validation_rejects_invalid_nested_array_item(self):
        result = LLMResult(
            text='{"repairs": ["plain text"]}',
            provider="test",
            model="test",
        )
        schema = {
            "type": "object",
            "properties": {
                "repairs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["zone_id"],
                    },
                },
            },
            "required": ["repairs"],
        }

        data, warnings = parse_json_response(result, schema)

        assert data is None
        assert any("$.repairs[0]" in warning for warning in warnings)


class TestFakeProvider:
    def test_returns_default_response(self):
        config = ProviderConfig(name="fake", protocol="fake", text_model="fake")
        provider = FakeProvider(config, simulate_latency_ms=0)
        result = provider.generate([LLMMessage.user("Tell me about document_analysis")])
        assert result.success
        data = json.loads(result.text)
        assert "project_name" in data

    def test_custom_response(self):
        config = ProviderConfig(name="fake", protocol="fake", text_model="fake")
        provider = FakeProvider(config, custom_responses={"hello": {"greeting": "world"}}, simulate_latency_ms=0)
        result = provider.generate([LLMMessage.user("hello there")])
        assert result.success
        assert result.json_data["greeting"] == "world"

    def test_simulated_error(self):
        config = ProviderConfig(name="fake", protocol="fake", text_model="fake")
        provider = FakeProvider(config, simulate_error="boom", simulate_latency_ms=0)
        result = provider.generate([LLMMessage.user("anything")])
        assert not result.success
        assert result.error == "boom"

    def test_simulated_invalid_json(self):
        config = ProviderConfig(name="fake", protocol="fake", text_model="fake")
        provider = FakeProvider(config, simulate_invalid_json=True, simulate_latency_ms=0)
        result = provider.generate([LLMMessage.user("anything")])
        assert result.success  # It returns a result, just with broken text
        assert "broken" in result.text

    def test_capabilities(self):
        config = ProviderConfig(name="fake", protocol="fake", text_model="fake")
        provider = FakeProvider(config)
        caps = provider.capabilities()
        assert caps.supports_json_mode is True
        assert caps.supports_vision is False


class TestLLMClient:
    def test_create_with_fake_provider(self):
        config = ProviderConfig(name="fake", protocol="fake", text_model="fake")
        provider = FakeProvider(config, simulate_latency_ms=0)
        client = LLMClient.from_provider(provider)
        assert client.capabilities.supports_json_mode

    def test_generate_text(self):
        config = ProviderConfig(name="fake", protocol="fake", text_model="fake")
        provider = FakeProvider(config, simulate_latency_ms=0)
        client = LLMClient.from_provider(provider)
        text = client.generate_text(prompt="Hello")
        assert text  # Should return something

    def test_generate_json_with_fallback(self):
        config = ProviderConfig(name="fake", protocol="fake", text_model="fake")
        provider = FakeProvider(config, simulate_error="fail", simulate_latency_ms=0)
        client = LLMClient.from_provider(provider)
        result = client.generate_json(
            prompt="Do something",
            fallback={"fallback": True},
        )
        assert result == {"fallback": True}

    def test_generate_json_success(self):
        config = ProviderConfig(name="fake", protocol="fake", text_model="fake")
        custom = {"test_prompt": {"result": "success", "data": [1, 2, 3]}}
        provider = FakeProvider(config, custom_responses=custom, simulate_latency_ms=0)
        client = LLMClient.from_provider(provider)
        result = client.generate_json(prompt="test_prompt please")
        assert result["result"] == "success"

    def test_generate_json_raises_without_fallback(self):
        config = ProviderConfig(name="fake", protocol="fake", text_model="fake")
        provider = FakeProvider(config, simulate_error="fail", simulate_latency_ms=0)
        client = LLMClient.from_provider(provider)
        with pytest.raises(RuntimeError):
            client.generate_json(prompt="Do something")

    def test_json_repair_reuses_callers_output_budget(self):
        class RecordingProvider(BaseLLMProvider):
            def __init__(self):
                super().__init__(
                    ProviderConfig(name="recording", protocol="fake", text_model="fake")
                )
                self.max_token_calls = []

            def capabilities(self):
                return ProviderCapabilities(supports_json_mode=True)

            def generate(self, messages, **kwargs):
                self.max_token_calls.append(kwargs.get("max_tokens"))
                if len(self.max_token_calls) == 1:
                    return LLMResult(
                        text='{"value": ',
                        provider="recording",
                        model="fake",
                        finish_reason="length",
                    )
                return LLMResult(
                    text='{"value": "fixed"}',
                    provider="recording",
                    model="fake",
                    finish_reason="stop",
                )

        provider = RecordingProvider()
        client = LLMClient.from_provider(provider)

        result = client.generate_json(
            prompt="Return JSON",
            schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            max_tokens=900,
        )

        assert result == {"value": "fixed"}
        assert provider.max_token_calls == [900, 900]

    def test_from_config_fake(self):
        # Should work with the default config which has fake provider
        client = LLMClient.from_config(profile="fake", config_path="/nonexistent.yml")
        assert client.provider.name == "fake"

    def test_create_provider_factory(self):
        config = ProviderConfig(name="fake", protocol="fake", text_model="fake")
        provider = _create_provider(config)
        assert provider.name == "fake"

    def test_create_provider_unknown_raises(self):
        config = ProviderConfig(name="bad", protocol="unknown_protocol", text_model="x")
        with pytest.raises(ValueError, match="Unknown protocol"):
            _create_provider(config)

    def test_deepseek_json_generation_does_not_advertise_json_schema_tools(self):
        config = ProviderConfig(
            name="deepseek", protocol="openai_chat_completions",
            endpoint="https://api.deepseek.com/v1",
            beta_endpoint="https://api.deepseek.com/beta",
            text_model="deepseek-v4-pro",
        )
        provider = OpenAICompatibleProvider(config)

        caps = provider.capabilities()

        assert caps.supports_json_mode is True
        assert caps.supports_json_schema is False
        assert caps.supports_strict_tools is True
        assert caps.supports_forced_tool_choice is True

    def test_deepseek_json_mode_sends_no_tools_or_tool_choice(self):
        config = ProviderConfig(
            name="deepseek", protocol="openai_chat_completions",
            endpoint="https://api.deepseek.com/v1",
            beta_endpoint="https://api.deepseek.com/beta",
            text_model="deepseek-v4-pro",
        )
        provider = OpenAICompatibleProvider(config)
        create = MagicMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
            usage=None,
        ))
        provider._client = SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)
        ))

        result = provider.generate(
            [LLMMessage.user("Return json")], json_mode=True
        )

        assert result.success
        kwargs = create.call_args.kwargs
        assert kwargs["response_format"] == {"type": "json_object"}
        assert kwargs["extra_body"] == {
            "thinking": {"type": "disabled"},
        }
        assert "tools" not in kwargs
        assert "tool_choice" not in kwargs

    def test_generate_json_with_schema_still_uses_deepseek_json_mode(self):
        config = ProviderConfig(
            name="deepseek", protocol="openai_chat_completions",
            endpoint="https://api.deepseek.com/v1",
            beta_endpoint="https://api.deepseek.com/beta",
            text_model="deepseek-v4-pro",
        )
        provider = OpenAICompatibleProvider(config)
        create = MagicMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"value": "ok"}'))],
            usage=None,
        ))
        provider._client = SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)
        ))
        client = LLMClient.from_provider(provider)

        result = client.generate_json(
            prompt="Return json", schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        )

        assert result == {"value": "ok"}
        kwargs = create.call_args.kwargs
        assert kwargs["response_format"] == {"type": "json_object"}
        assert "tools" not in kwargs
        assert "tool_choice" not in kwargs

    def test_fallback_is_audited_and_queryable(self, tmp_path: Path):
        config = ProviderConfig(name="fake", protocol="fake", text_model="fake")
        provider = FakeProvider(config, simulate_error="provider rejected request", simulate_latency_ms=0)
        client = LLMClient.from_provider(provider, job_root=tmp_path)

        result = client.generate_json(
            prompt="Return json", phase="content_mapping_0",
            fallback={"slides": []},
        )

        assert result == {"slides": []}
        assert client.was_fallback("content_mapping_0")
        calls = [json.loads(line) for line in (tmp_path / "model_calls.jsonl").read_text().splitlines()]
        assert [item["status"] for item in calls] == ["error", "fallback"]
        events = [json.loads(line) for line in (tmp_path / "history.jsonl").read_text().splitlines()]
        assert events[-1]["type"] == "llm_fallback"


class _EmptyThenJsonProvider(BaseLLMProvider):
    def __init__(self) -> None:
        super().__init__(ProviderConfig(name="sequence", protocol="fake", text_model="sequence"))
        self.calls = 0

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_json_mode=True)

    def generate(self, messages, **kwargs):
        self.calls += 1
        text = "" if self.calls == 1 else '{"value": "recovered"}'
        return LLMResult(text=text, provider=self.name, model="sequence")


def test_empty_json_mode_response_is_retried() -> None:
    provider = _EmptyThenJsonProvider()
    client = LLMClient.from_provider(provider)

    result = client.generate_json(
        prompt="Return json", schema={"required": ["value"]}
    )

    assert result == {"value": "recovered"}
    assert provider.calls == 2


class _InvalidSchemaThenValidProvider(BaseLLMProvider):
    def __init__(self) -> None:
        super().__init__(
            ProviderConfig(
                name="sequence",
                protocol="fake",
                text_model="sequence",
            )
        )
        self.calls = 0
        self.messages_by_call = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_json_mode=True)

    def generate(self, messages, **kwargs):
        self.calls += 1
        self.messages_by_call.append(messages)
        text = (
            '{"repairs": ["plain text"]}'
            if self.calls == 1
            else '{"repairs": [{"zone_id": "shape-1"}]}'
        )
        return LLMResult(text=text, provider=self.name, model="sequence")


def test_nested_schema_mismatch_is_repaired_and_diagnosed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("PPT_AGENT_DEBUG_LLM", "1")
    provider = _InvalidSchemaThenValidProvider()
    client = LLMClient.from_provider(provider, job_root=tmp_path)
    schema = {
        "type": "object",
        "properties": {
            "repairs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"zone_id": {"type": "string"}},
                    "required": ["zone_id"],
                },
            },
        },
        "required": ["repairs"],
    }

    result = client.generate_json(
        prompt="Return json",
        schema=schema,
        phase="content_mapping_micro_0_1",
    )

    assert result == {"repairs": [{"zone_id": "shape-1"}]}
    assert provider.calls == 2
    assert any(
        "Return json" in message.text()
        for message in provider.messages_by_call[1][:-1]
    )
    assert "Validation errors" in provider.messages_by_call[1][-1].text()
    records = [
        json.loads(line)
        for line in (tmp_path / "llm_diagnostics.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert records[0]["schema_valid"] is False
    assert records[0]["repair_item_types"] == ["str"]
    assert records[0]["invalid_repair_indexes"] == [0]
    assert records[1]["schema_valid"] is True
    assert records[1]["repair_item_types"] == ["dict"]


class TestAudit:
    def test_log_model_call(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = LLMResult(text="ok", provider="fake", model="fake", latency_ms=42)
            log_model_call(Path(tmpdir), "test_phase", result, prompt_summary="hello")
            log_path = Path(tmpdir) / "model_calls.jsonl"
            assert log_path.exists()
            record = json.loads(log_path.read_text().strip())
            assert record["phase"] == "test_phase"
            assert record["provider"] == "fake"
            assert record["latency_ms"] == 42
            assert record["status"] == "success"


class TestWorkflowWithFakeLLM:
    """Integration: run the workflow with fake LLM to verify the new code path."""

    def test_workflow_with_fake_provider_produces_artifacts(self):
        from ppt_agent.coordinator.phase_state import create_job
        from ppt_agent.coordinator.workflow import run_workflow
        from ppt_agent.models.artifacts import JobWorkspace

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a minimal input
            input_dir = Path(tmpdir) / "input"
            input_dir.mkdir()
            (input_dir / "test.md").write_text("# Test Project\n\nThis is a test.", encoding="utf-8")

            job_dir = Path(tmpdir) / "job"
            workspace = create_job(input_dir, job_dir)

            # Run with fake profile and force=True (skips approval gate)
            outputs = run_workflow(workspace, model_profile="fake", force=True)

            # All 9 artifacts plus final.pptx should be produced
            assert (job_dir / "source_summary.json").exists()
            assert (job_dir / "outline.json").exists()
            assert (job_dir / "slide_design_plan.json").exists()
            assert (job_dir / "slide_contents.json").exists()
            assert (job_dir / "final.pptx").exists()
            assert (job_dir / "validation_report.json").exists()

    def test_workflow_without_profile_uses_fallback(self):
        """Running without --model-profile should still work (deterministic fallback)."""
        from ppt_agent.coordinator.phase_state import create_job
        from ppt_agent.coordinator.workflow import run_workflow

        with tempfile.TemporaryDirectory() as tmpdir:
            input_dir = Path(tmpdir) / "input"
            input_dir.mkdir()
            (input_dir / "test.md").write_text("# Demo\n\nDemo content.", encoding="utf-8")

            job_dir = Path(tmpdir) / "job"
            workspace = create_job(input_dir, job_dir)
            outputs = run_workflow(workspace, force=True)  # No model_profile

            assert (job_dir / "final.pptx").exists()


def test_runtime_mapping_schema_binds_slide_template_and_zone_ids() -> None:
    from jsonschema import Draft202012Validator

    schema = build_content_mapping_schema(
        "tech.template",
        [{
            "slide_index": 4,
            "template_slide_index": 9,
            "zones": [{
                "zone_id": "slide-10/shape-7",
                "type": "title",
                "action": "replace_text",
            }],
        }],
    )
    wire_valid = {
        "template_id": "tech.template",
        "slides": {"slide_0": {
            "slide_index": 4,
            "template_slide_index": 9,
            "zones": {"zone_0": {
                "zone_id": "slide-10/shape-7",
                "type": "title",
                "content": "系统架构",
                "action": "replace_text",
                "placement_reason": "页面主标题",
                "source_block_ids": ["slide-4-title"],
                "transformation": "none",
                "fit_status": "fits",
            }},
        }},
    }
    Draft202012Validator(schema).validate(wire_valid)
    normalized = normalize_content_mapping_response(wire_valid)
    assert normalized["slides"][0]["zones"][0]["zone_id"] == "slide-10/shape-7"

    wrong_zone = json.loads(json.dumps(wire_valid, ensure_ascii=False))
    wrong_zone["slides"]["slide_0"]["zones"]["zone_0"]["zone_id"] = "slide-11/shape-7"
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(wrong_zone)

    wrong_template_page = json.loads(json.dumps(wire_valid, ensure_ascii=False))
    wrong_template_page["slides"]["slide_0"]["template_slide_index"] = 10
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(wrong_template_page)


def test_strict_tool_schema_is_sent_on_first_call_not_embedded_in_prompt() -> None:
    class RecordingStrictProvider(BaseLLMProvider):
        def __init__(self) -> None:
            super().__init__(
                ProviderConfig(name="strict", protocol="fake", text_model="fake")
            )
            self.calls: list[dict] = []

        def capabilities(self) -> ProviderCapabilities:
            return ProviderCapabilities(
                supports_json_mode=True,
                supports_strict_tools=True,
            )

        def generate(self, messages, **kwargs) -> LLMResult:
            self.calls.append({"messages": messages, "kwargs": kwargs})
            return LLMResult(
                text='{"value":"ok"}',
                provider="strict",
                model="fake",
            )

    provider = RecordingStrictProvider()
    client = LLMClient.from_provider(provider)
    schema = {
        "title": "strict_result",
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }

    assert client.generate_json(
        prompt="Generate the result.",
        json_schema=schema,
        schema_name="strict_result",
    ) == {"value": "ok"}

    assert len(provider.calls) == 1
    first_call = provider.calls[0]
    assert first_call["kwargs"]["json_schema"] is schema
    assert first_call["kwargs"]["schema_name"] == "strict_result"
    prompt_text = "\n".join(
        message.text() for message in first_call["messages"]
    )
    assert "Expected JSON shape example" not in prompt_text
    assert '"properties"' not in prompt_text


def test_strict_schema_retries_same_api_constraint_before_fallback() -> None:
    class SequenceStrictProvider(BaseLLMProvider):
        def __init__(self) -> None:
            super().__init__(
                ProviderConfig(name="strict", protocol="fake", text_model="fake")
            )
            self.calls: list[dict] = []

        def capabilities(self) -> ProviderCapabilities:
            return ProviderCapabilities(supports_strict_tools=True)

        def generate(self, messages, **kwargs) -> LLMResult:
            self.calls.append(kwargs)
            if len(self.calls) < 3:
                return LLMResult(
                    error="temporary connection error",
                    provider="strict",
                    model="fake",
                )
            return LLMResult(
                text='{"value":"recovered"}',
                provider="strict",
                model="fake",
            )

    provider = SequenceStrictProvider()
    client = LLMClient.from_provider(provider)
    schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }

    result = client.generate_json(
        prompt="Generate.",
        json_schema=schema,
        schema_name="strict_result",
        fallback={"value": "fallback"},
    )

    assert result == {"value": "recovered"}
    assert len(provider.calls) == 3
    assert all(call["json_schema"] is schema for call in provider.calls)


def test_strict_schema_definition_error_is_not_hidden_by_fallback() -> None:
    class RejectedSchemaProvider(BaseLLMProvider):
        def __init__(self) -> None:
            super().__init__(
                ProviderConfig(name="strict", protocol="fake", text_model="fake")
            )
            self.calls = 0

        def capabilities(self) -> ProviderCapabilities:
            return ProviderCapabilities(supports_strict_tools=True)

        def generate(self, messages, **kwargs) -> LLMResult:
            self.calls += 1
            return LLMResult(
                error="Invalid JSON Schema: minItems is unsupported",
                provider="strict",
                model="fake",
            )

    provider = RejectedSchemaProvider()
    client = LLMClient.from_provider(provider)
    schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }

    with pytest.raises(RuntimeError, match="fallback is intentionally disabled"):
        client.generate_json(
            prompt="Generate.",
            json_schema=schema,
            schema_name="strict_result",
            fallback={"value": "fallback"},
        )
    assert provider.calls == 1
