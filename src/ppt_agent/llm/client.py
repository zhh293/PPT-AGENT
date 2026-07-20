"""Unified LLM client.

This is the single entry point that Workers use to call language models.
It handles provider selection, JSON parsing, schema validation,
automatic repair, and audit logging.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ppt_agent.context.compression import compress_context
from ppt_agent.llm.audit import log_llm_fallback, log_model_call
from ppt_agent.llm.config import ModelConfig, ProviderConfig, load_model_config
from ppt_agent.llm.json_repair import repair_json
from ppt_agent.llm.messages import LLMMessage, LLMResult
from ppt_agent.llm.providers.base import BaseLLMProvider, ProviderCapabilities
from ppt_agent.llm.response_parser import parse_json_response

logger = logging.getLogger(__name__)


def _json_example_from_schema(schema: dict | None) -> dict:
    """Build a compact example so DeepSeek JSON Mode sees the desired shape."""
    if not schema:
        return {}
    if "anyOf" in schema:
        return _json_example_from_schema(schema["anyOf"][0])
    kind = schema.get("type")
    if kind == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", list(properties)[:6])
        return {
            key: _json_example_from_schema(properties.get(key, {}))
            for key in required[:10]
            if key in properties
        }
    if kind == "array":
        return [_json_example_from_schema(schema.get("items", {}))]
    if schema.get("enum"):
        return schema["enum"][0]
    return {"string": "example", "integer": 0, "number": 0.0,
            "boolean": True}.get(kind, "example")


def _create_provider(config: ProviderConfig) -> BaseLLMProvider:
    """Create the appropriate provider based on protocol."""
    if config.protocol == "fake":
        from ppt_agent.llm.providers.fake import FakeProvider
        return FakeProvider(config)
    elif config.protocol == "anthropic_messages":
        from ppt_agent.llm.providers.anthropic import AnthropicProvider
        return AnthropicProvider(config)
    elif config.protocol in ("openai_chat_completions", "openai_responses"):
        from ppt_agent.llm.providers.openai_compatible import OpenAICompatibleProvider
        return OpenAICompatibleProvider(config)
    else:
        raise ValueError(f"Unknown protocol: {config.protocol}")


class LLMClient:
    """Unified client for calling LLMs.

    Workers call this instead of any vendor SDK directly.

    Usage:
        client = LLMClient.from_config("anthropic")
        result = client.generate_json(
            prompt="Analyze this document...",
            schema=source_summary_schema,
            context={"text": document_text},
        )
    """

    # Approximate token-per-char ratio for context window estimation
    _CHARS_PER_TOKEN = 4

    # Default context window size (tokens) — overridden by provider config
    _DEFAULT_CONTEXT_WINDOW = 200_000

    # Compression thresholds matching §3.1 five-level spec
    _COMPRESSION_THRESHOLDS = [
        (0.95, 4),   # >95% → L4 emergency
        (0.85, 3),   # 85–95% → L3
        (0.70, 2),   # 70–85% → L2
        (0.50, 1),   # 50–70% → L1
    ]

    def __init__(
        self,
        provider: BaseLLMProvider,
        job_root: Path | None = None,
        context_window: int | None = None,
    ) -> None:
        self.provider = provider
        self.job_root = job_root
        self._capabilities = provider.capabilities()
        self._context_window = context_window or self._DEFAULT_CONTEXT_WINDOW
        self._fallback_phases: set[str] = set()

    def was_fallback(self, phase: str) -> bool:
        return phase in self._fallback_phases

    def _mark_fallback(self, phase: str, result: LLMResult) -> None:
        self._fallback_phases.add(phase)
        if self.job_root:
            log_llm_fallback(self.job_root, phase, result)

    @classmethod
    def from_config(
        cls,
        profile: str | None = None,
        config_path: str | Path = "config/models.yml",
        job_root: Path | None = None,
    ) -> LLMClient:
        """Create a client from config file and profile name."""
        model_config = load_model_config(config_path)
        provider_config = model_config.get_provider(profile)
        provider = _create_provider(provider_config)
        return cls(provider, job_root=job_root)

    @classmethod
    def from_provider(
        cls,
        provider: BaseLLMProvider,
        job_root: Path | None = None,
    ) -> LLMClient:
        """Create a client with an explicit provider instance."""
        return cls(provider, job_root=job_root)

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def generate_text(
        self,
        *,
        prompt: str,
        context: dict | None = None,
        system: str | None = None,
        phase: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Generate free-form text.

        Returns the text string. Raises on failure.
        """
        messages = self._build_messages(prompt, context, system)
        result = self.provider.generate(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        if self.job_root:
            log_model_call(self.job_root, phase, result, prompt_summary=prompt[:200])

        if not result.success:
            raise RuntimeError(f"LLM call failed: {result.error}")

        return result.text

    def generate_json(
        self,
        *,
        prompt: str,
        schema: dict | None = None,
        context: dict | None = None,
        system: str | None = None,
        phase: str = "",
        fallback: dict | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_schema: dict | None = None,
        schema_name: str = "",
    ) -> dict:
        """Generate structured JSON output.

        The prompt should instruct the model to output only JSON.
        The response is parsed, repaired if needed, and validated
        against the schema.

        When *json_schema* is provided, the provider uses structured
        output mode (``response_format: json_schema``) which guarantees
        the response matches the schema at the API level — no local
        JSON repair needed.

        Returns the parsed dict. Falls back to `fallback` if provided
        and parsing fails. Raises RuntimeError if no fallback.
        """
        effective_schema = schema or json_schema

        # DeepSeek JSON Mode requires the prompt to mention JSON and benefits
        # from a concrete example of the desired structure.
        json_system = (system or "") + "\n\nYou MUST respond with ONLY a valid JSON object. No markdown, no explanation, no code fences."
        if effective_schema:
            example = json.dumps(
                _json_example_from_schema(effective_schema),
                ensure_ascii=False,
            )
            json_system += f"\nExpected JSON shape example: {example[:4000]}"
        json_system = json_system.strip()

        messages = self._build_messages(prompt, context, json_system)

        # First attempt — use json_schema (strict) when the provider supports it,
        # otherwise fall back to json_object mode.
        _use_schema = (
            json_schema is not None
            and self._capabilities.supports_json_schema
        )
        result = self.provider.generate(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=self._capabilities.supports_json_mode if not _use_schema else False,
            json_schema=json_schema if _use_schema else None,
            schema_name=schema_name if _use_schema else "",
        )

        _audit_schema_name = (
            schema_name
            or (effective_schema.get("title", "") if effective_schema else "")
        )
        if self.job_root:
            log_model_call(
                self.job_root, phase, result,
                prompt_summary=prompt[:200],
                schema_name=_audit_schema_name,
            )

        if not result.success:
            logger.warning("LLM call failed: %s", result.error)
            if fallback is not None:
                self._mark_fallback(phase, result)
                return fallback
            raise RuntimeError(f"LLM call failed: {result.error}")

        # Parse and validate
        data, warnings = parse_json_response(result, effective_schema)

        if data is not None:
            if warnings:
                logger.info("JSON parse warnings: %s", warnings)
            return data

        # Repair attempt: ask the model to fix its own output
        logger.warning("JSON parse failed, attempting repair. Warnings: %s", warnings)
        repair_result = self._attempt_repair(result.text, messages, effective_schema)

        if self.job_root and repair_result:
            repair_result.repaired = True
            log_model_call(
                self.job_root, phase, repair_result,
                prompt_summary="[repair]",
                schema_name=_audit_schema_name,
            )

        if repair_result and repair_result.success:
            repair_data, repair_warnings = parse_json_response(repair_result, effective_schema)
            if repair_data is not None:
                logger.info("JSON repair succeeded. Warnings: %s", repair_warnings)
                return repair_data

        # All attempts failed
        logger.warning("JSON repair failed. Using fallback.")
        if fallback is not None:
            fallback_result = LLMResult(
                provider=result.provider,
                model=result.model,
                error="Model returned invalid or empty JSON and repair failed",
            )
            self._mark_fallback(phase, fallback_result)
            return fallback
        raise RuntimeError(
            f"Failed to get valid JSON from LLM after repair. "
            f"Warnings: {warnings}"
        )

    def _build_messages(
        self,
        prompt: str,
        context: dict | None,
        system: str | None,
    ) -> list[LLMMessage]:
        """Build the message list with automatic context compression.

        The 5-level compression (§3.1) is applied based on estimated
        context window utilization:
            L0 (<50%)  — no compression
            L1 (50-70%) — normalize whitespace, truncate long lists
            L2 (70-85%) — summarize evidence, truncate strings
            L3 (85-95%) — keep only phase-relevant data
            L4 (>95%)  — emergency: only errors + approved text
        """
        messages: list[LLMMessage] = []

        # Inject memory context into system prompt if available
        system_text = system or ""
        if hasattr(self, '_memory_context') and self._memory_context:
            system_text = f"{system_text}\n\n## Agent Memory\n\n{self._memory_context}"
            system_text = system_text.strip()

        # Inject loaded skill context if available
        if hasattr(self, '_skill_contexts') and self._skill_contexts:
            for phase, skill_info in self._skill_contexts.items():
                skill_content = skill_info.get('content', '')
                if skill_content:
                    system_text = f"{system_text}\n\n## Skill: {skill_info.get('skill_name', phase)}\n\n{skill_content}"

        if system_text:
            messages.append(LLMMessage.system(system_text))

        user_text = prompt
        if context:
            # Estimate current utilization and determine compression level
            level = self._determine_compression_level(system_text, prompt, context)
            if level > 0:
                logger.info("Applying L%d context compression (window utilization above threshold)", level)

            compressed = compress_context(context, level, llm_client=self)
            context_str = json.dumps(compressed, ensure_ascii=False, indent=2)
            user_text = f"{prompt}\n\n## Context\n\n{context_str}"

        messages.append(LLMMessage.user(user_text))
        return messages

    def _determine_compression_level(self, system_text: str, prompt: str, context: dict) -> int:
        """Estimate context window utilization and return the appropriate compression level.

        Uses character count as a proxy for token count (§3.1 thresholds).
        """
        # Estimate token usage from all components
        context_str = json.dumps(context, ensure_ascii=False)
        total_chars = len(system_text) + len(prompt) + len(context_str)
        estimated_tokens = total_chars / self._CHARS_PER_TOKEN
        utilization = estimated_tokens / self._context_window

        for threshold, level in self._COMPRESSION_THRESHOLDS:
            if utilization >= threshold:
                return level

        return 0  # L0: no compression

    def _attempt_repair(
        self,
        broken_text: str,
        original_messages: list[LLMMessage],
        schema: dict | None,
    ) -> LLMResult | None:
        """Ask the model to repair its own broken JSON output."""
        repair_prompt = (
            "Your previous response was not valid JSON. "
            "Here is what you returned:\n\n"
            f"```\n{broken_text[:2000]}\n```\n\n"
            "Please return ONLY a valid JSON object with no other text."
        )
        if schema and "required" in schema:
            repair_prompt += f"\n\nRequired fields: {schema['required']}"

        repair_messages = [
            LLMMessage.system("You are a JSON repair assistant. Output ONLY valid JSON."),
            LLMMessage.user(repair_prompt),
        ]

        try:
            return self.provider.generate(
                repair_messages,
                temperature=0.1,
                json_mode=self._capabilities.supports_json_mode,
            )
        except Exception as e:
            logger.warning("Repair call failed: %s", e)
            return None
