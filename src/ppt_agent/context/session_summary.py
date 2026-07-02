from __future__ import annotations

from pathlib import Path


def append_phase_summary(job_root: Path, phase: str, summary: str) -> None:
    with (Path(job_root) / "session.md").open("a", encoding="utf-8") as fh:
        fh.write(f"\n## {phase}\n\n{summary.strip()}\n")
