"""Three-layer memory system.

Layer 1 (agent.md)  — Immutable agent identity and capabilities.  Per-job.
Layer 2 (memory.md) — Cross-session knowledge, shared across jobs in the
                      same workspace.  Updated by dream tasks.
Layer 3 (session.md) — Current session state and phase summaries.  Per-job.

All layers are plain Markdown files.  agent.md and session.md live at
``<job_root>/.ppt_agent/``.  memory.md lives at the **workspace** level
so knowledge persists across jobs.  The workspace root is determined by
``PPT_AGENT_MEMORY_ROOT`` env var or ``<job_root>.parent``.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

MEMORY_DIR = ".ppt_agent"
MEMORY_FILES = {
    "agent": "agent.md",
    "memory": "memory.md",
    "session": "session.md",
}

_DEFAULT_AGENT_MD = """# PPT Generation Agent

## Identity
I am a PPT generation agent. I analyze project materials, generate structured
outlines, design professional slide layouts, and assemble editable PowerPoint
presentations.

## Capabilities
- Document analysis (text extraction, domain classification, evidence gathering)
- Outline generation (scene-based structure selection, narrative flow)
- Design planning (layout patterns, color themes, visual density)
- Content mapping (text compression, zone fitting, image-slide matching)
- Template-based PPT assembly with editable text zones
- Multi-format input support (Markdown, DOCX, CSV, images)

## Constraints
- I NEVER fabricate facts not present in source materials
- I ALWAYS preserve user-edited text verbatim during assembly
- I require user approval before final PPT generation
- I generate images only through the gptimage2-generator skill
"""


# ── Workspace-level memory root ───────────────────────────────────────

def get_workspace_memory_root(job_root: Path) -> Path:
    """Resolve the workspace-level memory root.

    Priority:
        1. ``PPT_AGENT_MEMORY_ROOT`` environment variable.
        2. ``job_root.parent`` (the workspace directory containing job dirs).

    Returns a Path to the ``.ppt_agent/`` directory for the workspace.
    """
    env_root = os.environ.get("PPT_AGENT_MEMORY_ROOT")
    if env_root:
        return Path(env_root) / MEMORY_DIR
    return job_root.resolve().parent / MEMORY_DIR


# ── Helpers ──────────────────────────────────────────────────────────

def ensure_memory_dir(workspace_root: Path) -> Path:
    """Ensure the memory directory exists and return its path."""
    memory_dir = workspace_root / MEMORY_DIR
    memory_dir.mkdir(parents=True, exist_ok=True)
    return memory_dir


def ensure_memory_files(workspace_root: Path) -> dict[str, Path]:
    """Ensure all three memory files exist with defaults."""
    memory_dir = ensure_memory_dir(workspace_root)
    paths = {}
    for key, filename in MEMORY_FILES.items():
        # memory.md is workspace-level; don't create a stale per-job copy
        if key == "memory":
            continue
        path = memory_dir / filename
        if not path.exists():
            if key == "agent":
                path.write_text(_DEFAULT_AGENT_MD.strip() + "\n", encoding="utf-8")
            elif key == "session":
                now = datetime.now(timezone.utc).isoformat()
                path.write_text(
                    f"# Session Log\n\nStarted: {now}\n\n", encoding="utf-8"
                )
        paths[key] = path
    return paths


# ── Read / Write ─────────────────────────────────────────────────────

def _memory_storage_path(workspace_root: Path, layer: str) -> Path:
    """Return the storage path for *layer*.

    agent.md and session.md → per-job (under workspace_root/.ppt_agent/)
    memory.md              → workspace-level (shared across jobs)
    """
    if layer == "memory":
        return get_workspace_memory_root(workspace_root) / "memory.md"
    return workspace_root / MEMORY_DIR / MEMORY_FILES[layer]


def read_memory(workspace_root: Path, layer: str) -> str:
    """Read a memory layer.

    For the "memory" layer, tries the workspace-level path first, then
    falls back to the per-job path for backward compatibility with old
    job data.
    """
    if layer not in MEMORY_FILES:
        raise ValueError(f"Unknown memory layer: {layer}. Valid: {list(MEMORY_FILES.keys())}")

    path = _memory_storage_path(workspace_root, layer)
    if path.exists():
        return path.read_text(encoding="utf-8")

    # Fallback: try the per-job path for "memory" (backward compat)
    if layer == "memory":
        legacy_path = workspace_root / MEMORY_DIR / MEMORY_FILES["memory"]
        if legacy_path.exists():
            logger.debug("Reading memory.md from legacy per-job path: %s", legacy_path)
            return legacy_path.read_text(encoding="utf-8")

    return ""


def append_memory(workspace_root: Path, layer: str, content: str) -> None:
    """Append content to a memory layer (memory or session only).

    "memory" writes to the workspace-level path so it is shared across
    all jobs in the same workspace.
    """
    if layer == "agent":
        raise ValueError("agent.md is immutable — cannot append.")

    if layer not in MEMORY_FILES:
        raise ValueError(f"Unknown memory layer: {layer}")

    path = _memory_storage_path(workspace_root, layer)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        if layer == "memory":
            path.write_text("# Cross-Session Memory\n", encoding="utf-8")
        else:
            ensure_memory_files(workspace_root)

    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"\n{content}\n")


# ── Context assembly ─────────────────────────────────────────────────

def build_memory_context(workspace_root: Path) -> str:
    """Assemble all three memory layers into a unified context string.

    Used to inject into the LLM system prompt at session start.
    Order: agent.md → memory.md → session.md
    """
    ensure_memory_files(workspace_root)

    parts = []
    agent_text = read_memory(workspace_root, "agent")
    if agent_text.strip():
        parts.append(agent_text.strip())

    memory_text = read_memory(workspace_root, "memory")
    if memory_text.strip() and "_No memories yet._" not in memory_text:
        parts.append(memory_text.strip())

    session_text = read_memory(workspace_root, "session")
    if session_text.strip():
        parts.append(session_text.strip())

    return "\n\n---\n\n".join(parts) if parts else ""
