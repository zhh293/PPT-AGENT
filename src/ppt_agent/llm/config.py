"""Configuration loader for LLM providers.

Reads config/models.yml and environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider."""

    name: str
    protocol: str  # "anthropic_messages" | "openai_chat_completions"
    api_key: str = ""  # Direct API key (takes precedence over env var)
    api_key_env: str = ""
    base_url_env: str = ""
    endpoint: str = ""
    beta_endpoint: str = ""   # e.g. https://api.deepseek.com/beta for strict tool calling
    text_model: str = ""
    vision_model: str = ""
    reasoning_model: str = ""
    temperature: float = 0.4
    max_tokens: int = 8000
    timeout_seconds: int = 90

    def get_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "")
        return ""

    def get_base_url(self) -> str | None:
        if self.endpoint:
            return self.endpoint
        if self.base_url_env:
            url = os.environ.get(self.base_url_env)
            if url:
                return url
        return None

    @property
    def supports_vision(self) -> bool:
        return bool(self.vision_model)


@dataclass
class ModelConfig:
    """Top-level model configuration."""

    default_provider: str = "fake"
    providers: dict[str, ProviderConfig] = field(default_factory=dict)

    def get_provider(self, name: str | None = None) -> ProviderConfig:
        key = name or self.default_provider
        if key not in self.providers:
            raise ValueError(
                f"Provider '{key}' not found. Available: {list(self.providers.keys())}"
            )
        return self.providers[key]


def load_model_config(path: str | Path = "config/models.yml") -> ModelConfig:
    """Load model configuration from YAML file.

    Falls back to a fake-only config if the file doesn't exist.
    """
    config_path = Path(path)
    if not config_path.exists():
        return _default_config()

    with config_path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    if not raw:
        return _default_config()

    providers: dict[str, ProviderConfig] = {}
    for name, prov_data in raw.get("providers", {}).items():
        providers[name] = ProviderConfig(
            name=name,
            protocol=prov_data.get("protocol", "openai_chat_completions"),
            api_key=prov_data.get("api_key", ""),
            api_key_env=prov_data.get("api_key_env", ""),
            base_url_env=prov_data.get("base_url_env", ""),
            endpoint=prov_data.get("endpoint", ""),
            beta_endpoint=prov_data.get("beta_endpoint", ""),
            text_model=prov_data.get("text_model", ""),
            vision_model=prov_data.get("vision_model", ""),
            reasoning_model=prov_data.get("reasoning_model", ""),
            temperature=prov_data.get("temperature", 0.4),
            max_tokens=prov_data.get("max_tokens", 8000),
            timeout_seconds=prov_data.get("timeout_seconds", 90),
        )

    return ModelConfig(
        default_provider=raw.get("default_provider", "fake"),
        providers=providers,
    )


def _default_config() -> ModelConfig:
    """Return a minimal config with only the fake provider."""
    return ModelConfig(
        default_provider="fake",
        providers={
            "fake": ProviderConfig(
                name="fake",
                protocol="fake",
                text_model="fake",
            )
        },
    )
