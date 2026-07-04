"""Query Router — 4-stage RAG pipeline (§8).

Implements the full retrieval pipeline:

    Stage 1: Query Enhancement (rewriting + multi-query expansion)
    Stage 2: Multi-Route Retrieval (BM25 + TF-IDF vector in parallel, RRF fusion)
    Stage 3: Reranking (heuristic cross-encoder-style scoring, top-N -> top-k)
    Stage 4: Context Assembly (relevance threshold, deduplication, source attribution)

The public ``query()`` function maintains backward compatibility with
the original signature while adding an optional ``llm_client`` parameter.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ppt_agent.retrieval.fusion.rrf import reciprocal_rank_fusion
from ppt_agent.retrieval.query_enhancement import (
    expand_query,
    heuristic_expand,
    rewrite_query,
)
from ppt_agent.retrieval.rerankers.base import Reranker
from ppt_agent.retrieval.sparse.bm25 import BM25Retriever
from ppt_agent.retrieval.vector.local import LocalVectorBackend

if TYPE_CHECKING:
    from ppt_agent.llm.client import LLMClient

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Pipeline configuration                                            #
# ------------------------------------------------------------------ #

# How many candidates to fetch from each retrieval path before fusion.
# Per §8.5: "Initial retrieval: Top-100 candidates (fast, approximate)"
_CANDIDATE_POOL = 100

# Minimum RRF score to survive into the reranking stage.
_MIN_RRF_SCORE = 0.0

# Minimum reranker score to survive into the final output (Stage 4 threshold).
_MIN_FINAL_SCORE = 0.0

# ------------------------------------------------------------------ #
#  Public API                                                        #
# ------------------------------------------------------------------ #

def query(
    documents: list[dict],
    query_text: str,
    methods: list[str] | None = None,
    top_k: int = 5,
    llm_client: "LLMClient | None" = None,
) -> list[dict]:
    """Execute the full 4-stage RAG pipeline.

    Parameters
    ----------
    documents
        List of document dicts, each with at least a ``"text"`` key.
    query_text
        The user's search query.
    methods
        Retrieval method hint (legacy).  The pipeline **always** runs
        hybrid BM25 + vector regardless of this parameter; it is retained
        for backward compatibility.
    top_k
        Number of final results to return.
    llm_client
        Optional LLM client.  When provided, Stage 1 uses LLM-based query
        rewriting and expansion.  When ``None``, enhancement is skipped.

    Returns a list of at most *top_k* result dicts with ``"score"``,
    ``"source"``, and ``"retrieval_method"`` fields added.
    """
    if not documents or not query_text.strip():
        return []

    # ==================================================================
    #  Stage 1: Query Enhancement
    # ==================================================================
    enhanced_query = rewrite_query(query_text, llm_client=llm_client)

    if llm_client is not None:
        expanded = expand_query(query_text, llm_client=llm_client)
    else:
        expanded = heuristic_expand(query_text)

    # All queries to run through retrieval (original/rewritten + expanded)
    all_queries = [enhanced_query]
    for eq in expanded:
        if eq not in all_queries:
            all_queries.append(eq)

    logger.debug(
        "Stage 1 — Query enhancement: %d queries from '%s' -> %s",
        len(all_queries), query_text, all_queries,
    )

    # ==================================================================
    #  Stage 2: Multi-Route Retrieval + RRF Fusion
    # ==================================================================
    bm25 = BM25Retriever(documents)
    vector = LocalVectorBackend(documents)

    # For each query, get BM25 + vector results, then fuse with RRF.
    # Merge across all queries by collecting fused results and re-fusing.
    all_fused_sets: list[list[dict]] = []

    for q in all_queries:
        # Fetch a broad candidate pool from each path (§8.5: top-100)
        pool = min(_CANDIDATE_POOL, len(documents))
        bm25_results = bm25.search(q, top_k=pool)
        vector_results = vector.search(q, top_k=pool)

        # RRF fusion of the two paths
        fused = reciprocal_rank_fusion([bm25_results, vector_results])
        if fused:
            all_fused_sets.append(fused)

    # Fuse across all queries (multi-query fusion)
    if len(all_fused_sets) == 1:
        fused_results = all_fused_sets[0]
    elif len(all_fused_sets) > 1:
        fused_results = reciprocal_rank_fusion(all_fused_sets)
    else:
        fused_results = []

    # Filter by minimum RRF score
    fused_results = [
        r for r in fused_results
        if r.get("score", 0) >= _MIN_RRF_SCORE
    ]

    logger.debug(
        "Stage 2 — Hybrid retrieval + RRF: %d candidates after fusion",
        len(fused_results),
    )

    # ==================================================================
    #  Stage 3: Reranking
    # ==================================================================
    reranker = Reranker()
    # Use the original query for reranking (user intent, not expanded)
    reranked = reranker.rerank(query_text, fused_results)

    logger.debug(
        "Stage 3 — Reranking: %d -> %d after reranking",
        len(fused_results), len(reranked),
    )

    # ==================================================================
    #  Stage 4: Context Assembly
    # ==================================================================
    final = _assemble_context(reranked, top_k, query_text)

    logger.debug(
        "Stage 4 — Context assembly: %d final results",
        len(final),
    )

    return final


# ------------------------------------------------------------------ #
#  Stage 4: Context Assembly                                         #
# ------------------------------------------------------------------ #

def _assemble_context(
    results: list[dict],
    top_k: int,
    query_text: str,
) -> list[dict]:
    """Apply relevance threshold, deduplication, and source attribution.

    - Filters out results below the minimum score.
    - Deduplicates by document id / text.
    - Adds ``source`` and ``retrieval_method`` metadata.
    - Truncates to *top_k*.
    """
    seen: set[str] = set()
    deduped: list[dict] = []

    for item in results:
        score = item.get("score", 0.0)
        if score < _MIN_FINAL_SCORE:
            continue

        # Dedup key: prefer id, then template_id, then text hash
        key = (
            item.get("id")
            or item.get("template_id")
            or item.get("text", "")[:200]
        )
        if key in seen:
            continue
        seen.add(key)

        # Source attribution
        enriched = {
            **item,
            "source": item.get("source", _infer_source(item)),
            "retrieval_method": "hybrid_bm25_tfidf_rrf_rerank",
        }
        deduped.append(enriched)

    return deduped[:top_k]


def _infer_source(item: dict) -> str:
    """Infer a human-readable source label from a result dict."""
    if item.get("template_id"):
        return f"template:{item['template_id']}"
    if item.get("id"):
        return f"doc:{item['id']}"
    if item.get("filename"):
        return f"file:{item['filename']}"
    return "unknown"
