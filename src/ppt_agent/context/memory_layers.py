"""Three-layer memory system.

Layer 1 (agent.md)  — Immutable agent identity and capabilities.
Layer 2 (memory.md) — Cross-session knowledge, updated by dream tasks.
Layer 3 (session.md) — Current session state and phase summaries.

All layers are plain Markdown files at workspace_root/.ppt_agent/.
"""

from __future__ import annotations

import logging
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
        path = memory_dir / filename
        if not path.exists():
            if key == "agent":
                path.write_text(_DEFAULT_AGENT_MD.strip() + "\n", encoding="utf-8")
            elif key == "memory":
                path.write_text("# Cross-Session Memory\n\n_No memories yet._\n", encoding="utf-8")
            elif key == "session":
                now = datetime.now(timezone.utc).isoformat()
                path.write_text(
                    f"# Session Log\n\nStarted: {now}\n\n", encoding="utf-8"
                )
        paths[key] = path
    return paths


def read_memory(workspace_root: Path, layer: str) -> str:
    """Read a memory layer. Returns empty string if not found."""
    filename = MEMORY_FILES.get(layer)
    if not filename:
        raise ValueError(f"Unknown memory layer: {layer}. Valid: {list(MEMORY_FILES.keys())}")
    path = workspace_root / MEMORY_DIR / filename
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def append_memory(workspace_root: Path, layer: str, content: str) -> None:
    """Append content to a memory layer (memory or session only)."""
    if layer == "agent":
        raise ValueError("agent.md is immutable — cannot append.")
    filename = MEMORY_FILES.get(layer)
    if not filename:
        raise ValueError(f"Unknown memory layer: {layer}")

    ensure_memory_dir(workspace_root)
    path = workspace_root / MEMORY_DIR / filename
    if not path.exists():
        ensure_memory_files(workspace_root)

    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"\n{content}\n")


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
