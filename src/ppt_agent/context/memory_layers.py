from __future__ import annotations

from pathlib import Path

MEMORY_FILES = ("agent.md", "memory.md", "session.md", "history.jsonl")


def ensure_memory_files(job_root: Path) -> None:
    for name in MEMORY_FILES:
        path = Path(job_root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)


def append_memory(job_root: Path, filename: str, content: str) -> None:
    if filename not in MEMORY_FILES:
        raise ValueError(f"Unsupported memory file: {filename}")
    with (Path(job_root) / filename).open("a", encoding="utf-8") as fh:
        fh.write(content.rstrip() + "\n")
