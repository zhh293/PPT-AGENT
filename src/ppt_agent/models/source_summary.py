from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class EvidenceItem:
    evidence_id: str
    summary: str
    source_refs: list[str]
    confidence: float = 0.7


@dataclass
class ImageInventoryItem:
    image_id: str
    path: str
    detected_usage: str = "unknown"
    summary: str = ""
    relevance: str = "supporting"


@dataclass
class SourceSummary:
    project_name: str
    domain: str = "general"
    target_audience: str = "business stakeholders"
    tone: str = "professional"
    value_proposition: str = ""
    product_capabilities: list[str] = field(default_factory=list)
    evidence_items: list[EvidenceItem] = field(default_factory=list)
    image_inventory: list[ImageInventoryItem] = field(default_factory=list)
    unsupported_claims: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 0.65

    def to_dict(self) -> dict:
        return asdict(self)
