from __future__ import annotations

from dataclasses import dataclass


@dataclass
class KnowledgeBaseConfig:
    name: str
    sources: list[dict]
    chunking: dict
    retrieval: dict
    description: str = ""
    embedding: dict | None = None
    refresh: dict | None = None
