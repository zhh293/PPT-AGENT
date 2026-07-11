"""Fake LLM provider for testing and CI.

Returns deterministic responses based on prompt content.
Can be configured to return invalid JSON, timeout, or errors.
"""

from __future__ import annotations

import json
import time
from typing import Any

from ppt_agent.llm.config import ProviderConfig
from ppt_agent.llm.messages import LLMMessage, LLMResult
from ppt_agent.llm.providers.base import BaseLLMProvider, ProviderCapabilities

# Default fake responses keyed by phase or prompt keyword
_DEFAULT_RESPONSES: dict[str, dict] = {
    "document_analysis": {
        "project_name": "Test Project",
        "domain": "software/product",
        "target_audience": "business stakeholders",
        "tone": "professional",
        "value_proposition": "A comprehensive solution for test scenarios.",
        "product_capabilities": [
            "Automated testing capability",
            "Data analysis and reporting",
            "User management system",
        ],
        "evidence_items": [
            {
                "evidence_id": "ev_1",
                "summary": "Project materials describe a software system.",
                "source_refs": ["input"],
                "confidence": 0.8,
            }
        ],
        "unsupported_claims": [],
        "warnings": [],
        "confidence": 0.85,
    },
    "outline_generation": {
        "meta": {
            "project_name": "Test Project",
            "domain": "software/product",
            "audience": "business stakeholders",
            "tone": "professional",
            "total_slides": 8,
            "assumptions": [],
            "needs_user_review": True,
        },
        "slides": [
            {
                "slide_index": i,
                "type": t,
                "title": title,
                "purpose": f"Purpose for slide {i}",
                "bullets": [f"Point {j+1} for {title}" for j in range(3)],
                "source_refs": ["ev_1"],
                "image_needs": "concept illustration",
                "priority": "required" if i in {0, 7} else "recommended",
            }
            for i, (t, title) in enumerate([
                ("cover", "Test Project"),
                ("background", "背景与机会"),
                ("problem", "核心问题"),
                ("solution", "解决方案"),
                ("product", "产品能力"),
                ("evidence", "支撑材料"),
                ("roadmap", "推进计划"),
                ("closing", "总结与期待"),
            ])
        ],
    },
    "design_planning": {
        "theme_profile": {
            "theme_id": "test.professional",
            "color_tokens": {
                "primary": "#1F4E79",
                "secondary": "#70AD47",
                "accent": "#F4B183",
                "background": "#FFFFFF",
                "text": "#1F2933",
                "muted": "#6B7280",
            },
            "typography_tokens": {
                "title_font": "Aptos Display",
                "body_font": "Aptos",
                "title_scale": 1.0,
                "body_scale": 1.0,
            },
            "spacing_tokens": {"page_margin": 0.08, "block_gap": 0.03, "card_padding": 0.02},
            "shape_tokens": {"border_radius": 0.02, "stroke": "light"},
            "image_treatment": {"crop": "contain", "tone": "natural"},
            "chart_style": {"palette": ["#1F4E79", "#70AD47", "#F4B183"]},
        },
        "slides": [],
        "global_style_notes": ["Test design plan."],
        "design_risks": [],
    },
    "content_mapping": {
        "slides": [],
    },
}


class FakeProvider(BaseLLMProvider):
    """Fake provider for testing.

    Behaviour can be configured:
    - custom_responses: dict mapping keywords to JSON responses
    - simulate_error: str error message to return
    - simulate_invalid_json: bool to return malformed JSON
    - simulate_latency_ms: int artificial delay
    """

    def __init__(
        self,
        config: ProviderConfig,
        custom_responses: dict[str, Any] | None = None,
        simulate_error: str | None = None,
        simulate_invalid_json: bool = False,
        simulate_latency_ms: int = 10,
    ) -> None:
        super().__init__(config)
        self.custom_responses = custom_responses or {}
        self.simulate_error = simulate_error
        self.simulate_invalid_json = simulate_invalid_json
        self.simulate_latency_ms = simulate_latency_ms

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_vision=False,
            supports_json_mode=True,
            supports_tool_use=False,
            supports_streaming=False,
            max_context_tokens=32_000,
        )

    def _find_response(self, messages: list[LLMMessage]) -> dict | str:
        """Find a matching response based on message content.

        Prioritizes the user/prompt messages over system messages to avoid
        false matches when memory/skill context is injected into the system prompt.
        """
        # Separate user messages from system messages for priority matching
        user_text = " ".join(msg.text() for msg in messages if msg.role != "system").lower()
        full_text = " ".join(msg.text() for msg in messages).lower()

        # Check custom responses first (against all text)
        for keyword, response in self.custom_responses.items():
            if keyword.lower() in full_text:
                return response

        # Check default responses — prioritize user message matches
        for keyword, response in _DEFAULT_RESPONSES.items():
            key_space = keyword.lower().replace("_", " ")
            key_under = keyword.lower()
            if key_space in user_text or key_under in user_text:
                return response

        # Fallback: check all text (including system prompt)
        for keyword, response in _DEFAULT_RESPONSES.items():
            key_space = keyword.lower().replace("_", " ")
            key_under = keyword.lower()
            if key_space in full_text or key_under in full_text:
                return response

        # Generic fallback
        return {"status": "ok", "message": "Fake LLM response"}

    def generate(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResult:
        if self.simulate_latency_ms > 0:
            time.sleep(self.simulate_latency_ms / 1000)

        if self.simulate_error:
            return LLMResult(
                provider=self.name,
                model="fake",
                latency_ms=self.simulate_latency_ms,
                error=self.simulate_error,
            )

        response_data = self._find_response(messages)

        if self.simulate_invalid_json:
            return LLMResult(
                text='{"broken json: missing bracket',
                provider=self.name,
                model="fake",
                latency_ms=self.simulate_latency_ms,
            )

        if isinstance(response_data, str):
            text = response_data
        else:
            text = json.dumps(response_data, ensure_ascii=False)

        return LLMResult(
            text=text,
            json_data=response_data if isinstance(response_data, dict) else None,
            provider=self.name,
            model="fake",
            latency_ms=self.simulate_latency_ms,
            prompt_tokens=100,
            completion_tokens=200,
        )
