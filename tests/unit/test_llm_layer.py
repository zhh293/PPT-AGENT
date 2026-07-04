"""Tests for the LLM abstraction layer.

Tests config loading, message conversion, JSON repair, fake provider,
and LLMClient integration with fake provider.
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from ppt_agent.llm.config import ModelConfig, ProviderConfig, load_model_config, _default_config
from ppt_agent.llm.messages import LLMMessage, ContentBlock, LLMResult
from ppt_agent.llm.json_repair import extract_json_object, repair_json
from ppt_agent.llm.response_parser import parse_json_response
from ppt_agent.llm.audit import log_model_call
from ppt_agent.llm.providers.fake import FakeProvider
from ppt_agent.llm.client import LLMClient, _create_provider


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
        assert data == {"a": 1}
        assert any("b" in w for w in warnings)


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
