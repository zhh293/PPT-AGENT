from __future__ import annotations


def chunk_text(text: str, max_chars: int = 1200, overlap: int = 100) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + max_chars])
        start += max(1, max_chars - overlap)
    return chunks or [""]
