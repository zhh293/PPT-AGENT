"""Session summary writer.

After each phase completes, appends a brief summary to session.md.
This keeps the session context up-to-date for subsequent phases.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ppt_agent.context.memory_layers import append_memory


def append_phase_summary(
    workspace_root: Path,
    phase: str,
    summary: str,
    artifact_name: str | None = None,
    warnings: list[str] | None = None,
) -> None:
    """Append a phase completion summary to session.md."""
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    parts = [f"### {phase} ({now})", "", summary]

    if artifact_name:
        parts.append(f"- Artifact: `{artifact_name}`")
    if warnings:
        parts.append(f"- Warnings: {', '.join(warnings)}")

    content = "\n".join(parts)
    append_memory(workspace_root, "session", content)
