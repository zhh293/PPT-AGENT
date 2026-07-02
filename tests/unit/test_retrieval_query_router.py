from __future__ import annotations

from ppt_agent.retrieval.query_router import query


def test_query_router_bm25() -> None:
    docs = [{"id": "a", "text": "business project template"}, {"id": "b", "text": "other"}]
    assert query(docs, "business", ["bm25"], 1)[0]["id"] == "a"
