"""Vendor-agnostic message model for LLM interactions.

All Workers use these types to build prompts. Providers convert them
to the vendor-specific wire format (Anthropic content blocks, OpenAI
messages, etc.).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ContentBlock:
    """A single content element inside a message."""

    type: Literal["text", "image", "json", "tool_result"]
    text: str | None = None
    path: str | None = None
    mime_type: str | None = None
    data: dict | None = None
    # For image blocks: base64-encoded bytes (set by helpers)
    image_data: str | None = None

    @classmethod
    def text_block(cls, text: str) -> ContentBlock:
        return cls(type="text", text=text)

    @classmethod
    def image_block(cls, path: str, mime_type: str = "image/png") -> ContentBlock:
        return cls(type="image", path=path, mime_type=mime_type)

    @classmethod
    def json_block(cls, data: dict) -> ContentBlock:
        import json
        return cls(type="json", text=json.dumps(data, ensure_ascii=False))

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class LLMMessage:
    """A single message in a conversation."""

    role: Literal["system", "user", "assistant", "tool"]
    content: list[ContentBlock]

    @classmethod
    def system(cls, text: str) -> LLMMessage:
        return cls(role="system", content=[ContentBlock.text_block(text)])

    @classmethod
    def user(cls, text: str) -> LLMMessage:
        return cls(role="user", content=[ContentBlock.text_block(text)])

    @classmethod
    def user_with_images(cls, text: str, image_paths: list[str]) -> LLMMessage:
        blocks: list[ContentBlock] = [ContentBlock.text_block(text)]
        for path in image_paths:
            blocks.append(ContentBlock.image_block(path))
        return cls(role="user", content=blocks)

    @classmethod
    def assistant(cls, text: str) -> LLMMessage:
        return cls(role="assistant", content=[ContentBlock.text_block(text)])

    def text(self) -> str:
        """Extract all text content concatenated."""
        parts = []
        for block in self.content:
            if block.text:
                parts.append(block.text)
        return "\n".join(parts)


@dataclass
class LLMResult:
    """The result returned from an LLM call."""

    text: str = ""
    json_data: dict | None = None
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    repaired: bool = False
    finish_reason: str = ""
    reasoning_text: str = ""
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None
