from __future__ import annotations

import math
import re
from collections import Counter


def tokenize(text: str) -> list[str]:
    return re.findall(r"[\w\u4e00-\u9fff]+", text.lower())


class BM25Retriever:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents
        self.tokens = [tokenize(doc.get("text", "")) for doc in documents]
        self.df = Counter(token for toks in self.tokens for token in set(toks))
        self.avgdl = sum(len(toks) for toks in self.tokens) / max(1, len(self.tokens))

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        q = tokenize(query)
        scored = []
        for doc, toks in zip(self.documents, self.tokens):
            tf = Counter(toks)
            score = 0.0
            for term in q:
                idf = math.log((len(self.documents) - self.df.get(term, 0) + 0.5) / (self.df.get(term, 0) + 0.5) + 1)
                denom = tf[term] + 1.5 * (1 - 0.75 + 0.75 * len(toks) / max(self.avgdl, 1))
                score += idf * (tf[term] * 2.5 / denom) if denom else 0
            scored.append({**doc, "score": score})
        return sorted(scored, key=lambda item: item["score"], reverse=True)[:top_k]
