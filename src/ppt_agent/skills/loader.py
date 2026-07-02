from __future__ import annotations

from pathlib import Path


def load_skill(skill_name: str, root: Path = Path(".catpaw/skills")) -> str:
    path = root / skill_name / "SKILL.md"
    if not path.exists():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")
