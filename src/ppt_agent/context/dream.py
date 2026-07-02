from __future__ import annotations

from pathlib import Path


def append_relevant_memory(job_root: Path, note: str, relevance: float) -> bool:
    if relevance < 0.6:
        return False
    with (Path(job_root) / "memory.md").open("a", encoding="utf-8") as fh:
        fh.write(note.strip() + "\n")
    return True
