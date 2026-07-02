from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    path: Path
    description: str = ""


def discover_skills(root: Path = Path(".catpaw/skills")) -> list[SkillMetadata]:
    return [SkillMetadata(path.name, path / "SKILL.md") for path in root.iterdir() if (path / "SKILL.md").exists()] if root.exists() else []
