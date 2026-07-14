"""Anthropic Messages API provider.

Converts internal LLMMessage/ContentBlock to Anthropic's content block
format, calls the Messages API, and converts the response back.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

from ppt_agent.llm.config import ProviderConfig
from ppt_agent.llm.messages import ContentBlock, LLMMessage, LLMResult
from ppt_agent.llm.providers.base import BaseLLMProvider, ProviderCapabilities


def _read_image_base64(path: str) -> tuple[str, str]:
    """Read an image file and return (base64_data, media_type)."""
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
    return data, media_type


class AnthropicProvider(BaseLLMProvider):
    """Provider for Anthropic Messages API (Claude)."""

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise ImportError(
                    "anthropic package is required for Anthropic provider. "
                    "Install with: pip install anthropic"
                )
            kwargs = {}
            api_key = self.config.get_api_key()
            if api_key:
                kwargs["api_key"] = api_key
            base_url = self.config.get_base_url()
            if base_url:
                kwargs["base_url"] = base_url
            kwargs["timeout"] = self.config.timeout_seconds
            self._client = anthropic.Anthropic(**kwargs)
        return self._client

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_vision=True,
            supports_json_mode=False,  # Anthropic uses prompt-based JSON
            supports_tool_use=True,
            supports_streaming=True,
            max_context_tokens=200_000,
        )

    def _convert_messages(
        self, messages: list[LLMMessage]
    ) -> tuple[str, list[dict]]:
        """Convert internal messages to Anthropic format.

        Returns (system_text, anthropic_messages).
        Anthropic requires system as a top-level parameter, not a message.
        """
        system_parts: list[str] = []
        anthropic_messages: list[dict] = []

        for msg in messages:
            if msg.role == "system":
                system_parts.append(msg.text())
                continue

            content_blocks: list[dict] = []
            for block in msg.content:
                if block.type == "text" and block.text:
                    content_blocks.append({"type": "text", "text": block.text})
                elif block.type == "json" and block.text:
                    content_blocks.append({"type": "text", "text": block.text})
                elif block.type == "image" and block.path:
                    try:
                        img_data, media_type = _read_image_base64(block.path)
                        content_blocks.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": block.mime_type or media_type,
                                "data": img_data,
                            },
                        })
                    except FileNotFoundError:
                        content_blocks.append({
                            "type": "text",
                            "text": f"[Image not found: {block.path}]",
                        })

            if content_blocks:
                anthropic_messages.append({
                    "role": msg.role,
                    "content": content_blocks,
                })

        system_text = "\n\n".join(system_parts)
        return system_text, anthropic_messages

    def generate(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
        json_schema: dict | None = None,
        schema_name: str = "",
    ) -> LLMResult:
        client = self._get_client()
        system_text, api_messages = self._convert_messages(messages)

        model = self.config.text_model
        # Use vision model if any message contains images
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
        if system_text:
            kwargs["system"] = system_text
        temp = temperature if temperature is not None else self.config.temperature
        if temp is not None:
            kwargs["temperature"] = temp

        start = time.monotonic()
        try:
            response = client.messages.create(**kwargs)
        except Exception as e:
            elapsed = int((time.monotonic() - start) * 1000)
            return LLMResult(
                provider=self.name,
                model=model,
                latency_ms=elapsed,
                error=str(e),
            )

        elapsed = int((time.monotonic() - start) * 1000)

        # Extract text from response
        text_parts = []
        for block in response.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)
        result_text = "\n".join(text_parts)

        return LLMResult(
            text=result_text,
            provider=self.name,
            model=model,
            latency_ms=elapsed,
            prompt_tokens=getattr(response.usage, "input_tokens", 0),
            completion_tokens=getattr(response.usage, "output_tokens", 0),
        )
