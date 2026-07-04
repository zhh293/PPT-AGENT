"""Query Enhancement — Stage 1 of the 4-stage RAG pipeline (§8.4).

Provides query rewriting and multi-query expansion.  When an
``llm_client`` (``ppt_agent.llm.client.LLMClient``) is available the
enhancement uses the LLM; otherwise it degrades gracefully to identity /
empty expansion so callers without an LLM still get correct results.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ppt_agent.llm.client import LLMClient

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Tokenisation (shared with bm25.py for consistency)                #
# ------------------------------------------------------------------ #
_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


# ------------------------------------------------------------------ #
#  Query Rewriting  (§8.4.1)                                         #
# ------------------------------------------------------------------ #

_REWRITE_SYSTEM = (
    "You are a search query optimiser.  Rewrite the user's query into a "
    "concise, keyword-rich form optimised for document retrieval.  "
    "Remove filler words, expand abbreviations, and add synonyms.  "
    "Respond with ONLY the rewritten query — no explanation."
)


def rewrite_query(query: str, llm_client: "LLMClient | None" = None) -> str:
    """Rewrite a colloquial query into a retrieval-optimised form.

    When *llm_client* is ``None`` the original query is returned unchanged
    (identity fallback).
    """
    if llm_client is None:
        return query

    try:
        rewritten = llm_client.generate_text(
            prompt=query,
            system=_REWRITE_SYSTEM,
            temperature=0.0,
            max_tokens=256,
        ).strip()
        # Guard against empty / whitespace-only responses
        if rewritten:
            return rewritten
    except Exception:
        logger.warning("LLM query rewrite failed, using original query", exc_info=True)

    return query


# ------------------------------------------------------------------ #
#  Multi-Query Expansion  (§8.4.2)                                   #
# ------------------------------------------------------------------ #

_EXPAND_SYSTEM = (
    "You are a search query expansion assistant.  Given a user query, "
    "generate 2 to 4 complementary search queries, each targeting a "
    "different facet of the information need.  Respond with ONLY a JSON "
    'object: {"queries": ["query1", "query2", ...]}'
)


def expand_query(query: str, llm_client: "LLMClient | None" = None) -> list[str]:
    """Generate 2-4 complementary queries targeting different facets.

    Returns an empty list when no LLM is available or when expansion fails.
    The caller can treat an empty list as "no expansion — use original only".
    """
    if llm_client is None:
        return []

    try:
        data = llm_client.generate_json(
            prompt=f"Original query: {query}",
            system=_EXPAND_SYSTEM,
            schema={
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["queries"],
            },
            fallback={"queries": []},
            temperature=0.3,
            max_tokens=512,
        )
        queries = data.get("queries", [])
        # Filter out empties and the original query (avoid redundancy)
        return [q.strip() for q in queries if q.strip() and q.strip() != query]
    except Exception:
        logger.warning("LLM query expansion failed", exc_info=True)
        return []


# ------------------------------------------------------------------ #
#  Heuristic expansion (no-LLM fallback)                            #
# ------------------------------------------------------------------ #

def heuristic_expand(query: str) -> list[str]:
    """Generate simple variant queries without an LLM.

    Produces:
    - A keyword-only variant (strips stopwords)
    - A bigram-pairing variant for multi-term queries
    """
    tokens = _tokenize(query)
    if not tokens:
        return []

    stop = {"the", "a", "an", "is", "are", "of", "to", "in", "on", "for", "how", "what", "why", "where", "and", "or"}
    keywords = [t for t in tokens if t not in stop]
    variants: list[str] = []

    if keywords and keywords != tokens:
        variants.append(" ".join(keywords))

    if len(keywords) >= 3:
        # Pair-wise variant for broad recall
        variants.append(" ".join(keywords[:2]))
        variants.append(" ".join(keywords[1:]))

    return variants[:4]
