"""Heuristic Reranker — §8.5.

Without an external cross-encoder model, this module implements a
lightweight heuristic reranker that scores (query, document) pairs
using four signals:

1. **Exact phrase overlap** — bonus when the full query string appears
   verbatim in the document.
2. **Query term coverage** — what percentage of query terms appear in
   the document.
3. **Position weighting** — terms appearing earlier in the document
   receive higher weight (inverted position decay).
4. **Length normalization** — penalises overly long documents to avoid
   bias toward verbosity.

The final score is a weighted combination of these signals, normalised
to ``[0, 1]``.
"""

from __future__ import annotations

import math
import re
from collections import Counter

# ------------------------------------------------------------------ #
#  Tokenisation (shared with bm25.py for consistency)                #
# ------------------------------------------------------------------ #
_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


# ------------------------------------------------------------------ #
#  Reranker                                                          #
# ------------------------------------------------------------------ #

class Reranker:
    """Heuristic cross-encoder-style reranker.

    Scores each (query, document) pair and re-sorts the result list.
    Documents below ``min_score`` are discarded.
    """

    def __init__(
        self,
        *,
        min_score: float = 0.01,
        phrase_weight: float = 0.35,
        coverage_weight: float = 0.35,
        position_weight: float = 0.15,
        length_weight: float = 0.15,
    ) -> None:
        self.min_score = min_score
        self.phrase_weight = phrase_weight
        self.coverage_weight = coverage_weight
        self.position_weight = position_weight
        self.length_weight = length_weight

    def rerank(self, query: str, results: list[dict]) -> list[dict]:
        """Re-score and re-sort *results* based on query-document relevance.

        Each result dict must have a ``"text"`` key.  The ``"score"``
        field is overwritten with the new reranker score.
        """
        if not results:
            return []

        query_terms = _tokenize(query)
        query_lower = query.lower().strip()

        scored: list[tuple[float, dict]] = []
        for item in results:
            doc_text = item.get("text", "")
            doc_lower = doc_text.lower()

            # Signal 1: Exact phrase overlap
            phrase_score = self._phrase_overlap(query_lower, doc_lower)

            # Signal 2: Query term coverage
            coverage_score = self._term_coverage(query_terms, doc_lower)

            # Signal 3: Position weighting
            position_score = self._position_weighting(query_terms, doc_lower)

            # Signal 4: Length normalization
            length_score = self._length_norm(doc_text)

            final = (
                self.phrase_weight * phrase_score
                + self.coverage_weight * coverage_score
                + self.position_weight * position_score
                + self.length_weight * length_score
            )

            scored.append((final, {**item, "score": final, "rerank_score": final}))

        # Sort by reranker score descending
        scored.sort(key=lambda pair: pair[0], reverse=True)

        # Filter by minimum score threshold
        return [item for score, item in scored if score >= self.min_score]

    # ------------------------------------------------------------------ #
    #  Signal computations                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _phrase_overlap(query_lower: str, doc_lower: str) -> float:
        """1.0 if the exact query string appears in the doc, scaled by
        how much of the doc it occupies."""
        if not query_lower:
            return 0.0
        if query_lower in doc_lower:
            # Bonus proportional to query length relative to doc length
            ratio = len(query_lower) / max(len(doc_lower), 1)
            return min(1.0, 0.7 + ratio * 0.3)
        # Partial: check for longest common substring
        query_terms = _tokenize(query_lower)
        if not query_terms:
            return 0.0
        # Check multi-word phrases
        max_phrase_len = 0
        for i in range(len(query_terms)):
            for j in range(i + 1, len(query_terms) + 1):
                phrase = " ".join(query_terms[i:j])
                if phrase in doc_lower:
                    max_phrase_len = max(max_phrase_len, len(query_terms[i:j]))
        if max_phrase_len == 0:
            return 0.0
        return max_phrase_len / len(query_terms) * 0.5

    @staticmethod
    def _term_coverage(query_terms: list[str], doc_lower: str) -> float:
        """What fraction of query terms appear in the document."""
        if not query_terms:
            return 0.0
        doc_tokens = set(_tokenize(doc_lower))
        found = sum(1 for t in query_terms if t in doc_tokens)
        return found / len(query_terms)

    @staticmethod
    def _position_weighting(query_terms: list[str], doc_lower: str) -> float:
        """Terms appearing earlier in the document get higher weight.

        Uses an exponential decay: a term at position 0 gets weight 1.0,
        a term at the end gets weight ~0.1.
        """
        if not query_terms:
            return 0.0
        doc_tokens = _tokenize(doc_lower)
        if not doc_tokens:
            return 0.0
        total_len = len(doc_tokens)
        # Build a position lookup: first occurrence of each token
        first_pos: dict[str, int] = {}
        for idx, tok in enumerate(doc_tokens):
            if tok not in first_pos:
                first_pos[tok] = idx

        weights: list[float] = []
        for term in query_terms:
            if term in first_pos:
                pos = first_pos[term]
                # Exponential decay: e^(-3 * pos/total_len)
                decay = math.exp(-3.0 * pos / max(total_len, 1))
                weights.append(decay)
            else:
                weights.append(0.0)
        return sum(weights) / len(weights)

    @staticmethod
    def _length_norm(doc_text: str) -> float:
        """Normalise by document length.

        Sweet spot around 200-800 characters.  Very short docs lack
        context; very long docs dilute relevance.
        """
        length = len(doc_text)
        if length == 0:
            return 0.0
        if length <= 100:
            return length / 100 * 0.5
        if length <= 200:
            return 0.5 + (length - 100) / 100 * 0.5
        if length <= 800:
            return 1.0
        if length <= 2000:
            return 1.0 - (length - 800) / 1200 * 0.3
        return max(0.3, 0.7 - (length - 2000) / 5000 * 0.2)
