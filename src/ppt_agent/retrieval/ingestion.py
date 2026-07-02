from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SourceDocument:
    doc_id: str
    text: str
    metadata: dict
