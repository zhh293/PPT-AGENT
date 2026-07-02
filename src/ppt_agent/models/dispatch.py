from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class DispatchRequest:
    job_id: str
    phase: str
    capability: str
    inputs: list[str]
    constraints: dict = field(default_factory=dict)
    user_decisions: dict = field(default_factory=dict)
    context_policy: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DispatchDecision:
    job_id: str
    phase: str
    worker: str
    expected_outputs: list[str]
    skill: str | None = None
    knowledge_base: str | None = None
    permission_profile: str = "workspace-write"
    context_bundle: dict = field(default_factory=dict)
    retry_policy: dict = field(default_factory=lambda: {"max_attempts": 1})

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}
