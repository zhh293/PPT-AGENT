"""LLM abstraction layer for PPT Agent.

Provides a unified interface for calling language models across different
providers (Anthropic, OpenAI-compatible, fake/test), with JSON schema
validation, automatic repair, and audit logging.
"""

from ppt_agent.llm.client import LLMClient
from ppt_agent.llm.config import load_model_config, ModelConfig, ProviderConfig
from ppt_agent.llm.messages import LLMMessage, ContentBlock, LLMResult

__all__ = [
    "LLMClient",
    "load_model_config",
    "ModelConfig",
    "ProviderConfig",
    "LLMMessage",
    "ContentBlock",
    "LLMResult",
]
