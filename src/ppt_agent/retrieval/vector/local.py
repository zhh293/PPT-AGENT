from __future__ import annotations

from ppt_agent.retrieval.vector.base import VectorBackend


class LocalVectorBackend(VectorBackend):
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = documents or []

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        terms = set(query.lower().split())
        scored = []
        for doc in self.documents:
            text = doc.get("text", "").lower()
            score = sum(1 for term in terms if term in text) / max(1, len(terms))
            scored.append({**doc, "score": score})
        return sorted(scored, key=lambda item: item["score"], reverse=True)[:top_k]
