"""OpenAI-compatible API provider.

Works with OpenAI, model gateways, local vLLM/Ollama/LM Studio, etc.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

from ppt_agent.llm.config import ProviderConfig
from ppt_agent.llm.messages import ContentBlock, LLMMessage, LLMResult
from ppt_agent.llm.providers.base import BaseLLMProvider, ProviderCapabilities


def _read_image_data_url(path: str) -> str:
    """Read image and return as data URL for OpenAI vision."""
    p = Path(path)
    suffix = p.suffix.lower()
    media_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    media_type = media_map.get(suffix, "image/png")
    data = base64.standard_b64encode(p.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{data}"


class OpenAICompatibleProvider(BaseLLMProvider):
    """Provider for OpenAI-compatible chat completions API."""

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import openai
            except ImportError:
                raise ImportError(
                    "openai package is required for OpenAI-compatible provider. "
                    "Install with: pip install openai"
                )
            kwargs = {}
            api_key = self.config.get_api_key()
            if api_key:
                kwargs["api_key"] = api_key
            base_url = self.config.get_base_url()
            if base_url:
                kwargs["base_url"] = base_url
            kwargs["timeout"] = self.config.timeout_seconds
            self._client = openai.OpenAI(**kwargs)
        return self._client

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_vision=self.config.supports_vision,
            supports_json_mode=True,
            supports_tool_use=True,
            supports_streaming=True,
            max_context_tokens=128_000,
        )

    def _convert_messages(self, messages: list[LLMMessage]) -> list[dict]:
        """Convert internal messages to OpenAI chat format."""
        openai_messages: list[dict] = []

        for msg in messages:
            # Check if this message has only text blocks
            has_non_text = any(b.type == "image" for b in msg.content)

            if not has_non_text:
                # Simple text message
                openai_messages.append({
                    "role": msg.role,
                    "content": msg.text(),
                })
            else:
                # Multi-part content with images
                parts: list[dict] = []
                for block in msg.content:
                    if block.type in ("text", "json") and block.text:
                        parts.append({"type": "text", "text": block.text})
                    elif block.type == "image" and block.path:
                        try:
                            data_url = _read_image_data_url(block.path)
                            parts.append({
                                "type": "image_url",
                                "image_url": {"url": data_url},
                            })
                        except FileNotFoundError:
                            parts.append({
                                "type": "text",
                                "text": f"[Image not found: {block.path}]",
                            })

                openai_messages.append({
                    "role": msg.role,
                    "content": parts,
                })

        return openai_messages

    def generate(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResult:
        client = self._get_client()
        api_messages = self._convert_messages(messages)

        model = self.config.text_model
        if self.config.vision_model:
            for msg in messages:
                if any(b.type == "image" for b in msg.content):
                    model = self.config.vision_model
                    break

        kwargs: dict = {
            "model": model,
            "messages": api_messages,
            "max_tokens": max_tokens or self.config.max_tokens,
        }
        temp = temperature if temperature is not None else self.config.temperature
        if temp is not None:
            kwargs["temperature"] = temp
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        start = time.monotonic()
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as e:
            elapsed = int((time.monotonic() - start) * 1000)
            return LLMResult(
                provider=self.name,
                model=model,
                latency_ms=elapsed,
                error=str(e),
            )

        elapsed = int((time.monotonic() - start) * 1000)

        choice = response.choices[0]
        result_text = choice.message.content or ""
        usage = response.usage

        return LLMResult(
            text=result_text,
            provider=self.name,
            model=model,
            latency_ms=elapsed,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
        )
