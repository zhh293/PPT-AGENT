"""TF-IDF Vector Backend — §8.3.1 (local, no external model).

Implements a proper TF-IDF vectorizer with cosine similarity as the
"dense" retrieval path.  This complements BM25 (exact term matching)
with term-weighting-based semantic similarity, forming a legitimate
sparse-dense hybrid without requiring external embedding models.

The implementation follows the standard TF-IDF formulation:

    tf(t, d)   = count(t, d) / max_term_count(d)        (normalised TF)
    idf(t)     = log( (N + 1) / (df(t) + 1) ) + 1       (smoothed IDF)
    tfidf(t,d) = tf(t, d) * idf(t)
    cos_sim    = dot(q, d) / (||q|| * ||d||)
"""

from __future__ import annotations

import math
import re
from collections import Counter

from ppt_agent.retrieval.vector.base import VectorBackend

# ------------------------------------------------------------------ #
#  Tokenisation (shared with bm25.py for consistency)                #
# ------------------------------------------------------------------ #
_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


# ------------------------------------------------------------------ #
#  TF-IDF Vector Backend                                             #
# ------------------------------------------------------------------ #

class LocalVectorBackend(VectorBackend):
    """TF-IDF based vector similarity backend.

    Builds an in-memory TF-IDF representation of all documents at
    construction time and answers queries via cosine similarity.
    """

    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents: list[dict] = documents or []
        self._doc_tokens: list[list[str]] = []
        self._doc_tf: list[dict[str, float]] = []
        self._idf: dict[str, float] = {}
        self._doc_tfidf: list[dict[str, float]] = []
        self._doc_norms: list[float] = []
        self._vocab: set[str] = set()
        if self.documents:
            self._build_index()

    # ------------------------------------------------------------------ #
    #  Index construction                                               #
    # ------------------------------------------------------------------ #

    def _build_index(self) -> None:
        n_docs = len(self.documents)
        df: Counter = Counter()

        # Pass 1: tokenize and compute normalised TF
        for doc in self.documents:
            tokens = _tokenize(doc.get("text", ""))
            self._doc_tokens.append(tokens)
            if not tokens:
                self._doc_tf.append({})
                continue
            counts = Counter(tokens)
            max_count = max(counts.values())
            tf = {term: count / max_count for term, count in counts.items()}
            self._doc_tf.append(tf)
            for term in counts:
                df[term] += 1

        # Build vocabulary and IDF
        self._vocab = set(df.keys())
        for term, doc_freq in df.items():
            # Smoothed IDF: log((N + 1) / (df + 1)) + 1
            self._idf[term] = math.log((n_docs + 1) / (doc_freq + 1)) + 1

        # Pass 2: compute TF-IDF vectors and norms
        for tf in self._doc_tf:
            tfidf: dict[str, float] = {}
            for term, tf_val in tf.items():
                weight = tf_val * self._idf.get(term, 0.0)
                if weight > 0:
                    tfidf[term] = weight
            self._doc_tfidf.append(tfidf)
            norm = math.sqrt(sum(w * w for w in tfidf.values()))
            self._doc_norms.append(norm if norm > 0 else 1.0)

    # ------------------------------------------------------------------ #
    #  Search                                                           #
    # ------------------------------------------------------------------ #

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """Return the top-k documents ranked by cosine similarity."""
        if not self.documents:
            return []

        query_tfidf = self._compute_query_tfidf(query)
        query_norm = math.sqrt(sum(w * w for w in query_tfidf.values()))
        if query_norm == 0:
            # No known terms in query — return unranked (score 0)
            return [{**doc, "score": 0.0} for doc in self.documents[:top_k]]
        query_norm = query_norm  # normalise

        scored: list[tuple[float, dict]] = []
        for i, doc in enumerate(self.documents):
            doc_tfidf = self._doc_tfidf[i]
            doc_norm = self._doc_norms[i]

            # Cosine similarity = dot product / (||q|| * ||d||)
            # Iterate over the smaller vector for efficiency
            if len(query_tfidf) <= len(doc_tfidf):
                dot = sum(
                    weight * doc_tfidf.get(term, 0.0)
                    for term, weight in query_tfidf.items()
                )
            else:
                dot = sum(
                    weight * query_tfidf.get(term, 0.0)
                    for term, weight in doc_tfidf.items()
                )

            sim = dot / (query_norm * doc_norm) if doc_norm > 0 else 0.0
            scored.append((sim, {**doc, "score": sim}))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:top_k]]

    # ------------------------------------------------------------------ #
    #  Helpers                                                          #
    # ------------------------------------------------------------------ #

    def _compute_query_tfidf(self, query: str) -> dict[str, float]:
        """Compute the TF-IDF vector for a query string."""
        tokens = _tokenize(query)
        if not tokens:
            return {}
        counts = Counter(tokens)
        max_count = max(counts.values())
        tf = {term: count / max_count for term, count in counts.items()}
        # Use pre-computed IDF from the corpus
        return {
            term: tf_val * self._idf.get(term, 0.0)
            for term, tf_val in tf.items()
            if self._idf.get(term, 0.0) > 0
        }

    def add_documents(self, documents: list[dict]) -> None:
        """Add documents and rebuild the index."""
        self.documents.extend(documents)
        self._doc_tokens = []
        self._doc_tf = []
        self._idf = {}
        self._doc_tfidf = []
        self._doc_norms = []
        self._vocab = set()
        if self.documents:
            self._build_index()
