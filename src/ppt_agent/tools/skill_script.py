from __future__ import annotations

from pathlib import Path


def skill_script_path(skill_name: str, script_name: str, skills_root: Path = Path(".catpaw/skills")) -> Path:
    path = skills_root / skill_name / "scripts" / script_name
    if not path.exists():
        raise FileNotFoundError(path)
    return path
