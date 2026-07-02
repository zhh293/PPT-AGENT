from __future__ import annotations


def enrich_chunk(text: str, context: str = "") -> str:
    return f"{context}\n{text}".strip()
