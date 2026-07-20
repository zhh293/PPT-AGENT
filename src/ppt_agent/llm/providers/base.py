"""Base class for LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ppt_agent.llm.config import ProviderConfig
from ppt_agent.llm.messages import LLMMessage, LLMResult


@dataclass(frozen=True)
class ProviderCapabilities:
    """Advertised capabilities of a provider."""

    supports_vision: bool = False
    supports_json_mode: bool = False
    supports_json_schema: bool = False   # response_format: json_schema (strict)
    supports_tool_use: bool = False
    supports_strict_tools: bool = False
    supports_forced_tool_choice: bool = False
    supports_streaming: bool = False
    max_context_tokens: int = 128_000


class BaseLLMProvider(ABC):
    """Abstract base for all LLM providers.

    Each provider converts internal LLMMessage objects to the vendor
    wire format, sends the request, and converts the response back
    to a unified LLMResult.
    """

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    @property
    def name(self) -> str:
        return self.config.name

    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        ...

    @abstractmethod
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
        """Send messages to the model and return a result.

        When *json_schema* is provided, the provider uses structured output
        mode (``response_format: json_schema``) to guarantee the response
        matches the schema.  *schema_name* is a short identifier for the
        schema (e.g. "source_summary", "outline").
        """
        ...
