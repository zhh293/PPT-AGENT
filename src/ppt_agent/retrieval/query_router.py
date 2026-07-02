from __future__ import annotations

from ppt_agent.retrieval.sparse.bm25 import BM25Retriever
from ppt_agent.retrieval.vector.local import LocalVectorBackend


def query(documents: list[dict], query_text: str, methods: list[str] | None = None, top_k: int = 5) -> list[dict]:
    methods = methods or ["bm25"]
    results: list[dict] = []
    if "bm25" in methods:
        results.extend(BM25Retriever(documents).search(query_text, top_k=top_k))
    elif "vector" in methods:
        results.extend(LocalVectorBackend(documents).search(query_text, top_k=top_k))
    return results[:top_k]
