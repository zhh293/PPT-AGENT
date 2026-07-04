"""Skill loading with progressive disclosure.

Phase 1: Load only skill names and descriptions (for system prompt inventory).
Phase 2: Load full SKILL.md content on demand (when a phase needs it).

Tracks loaded skills per session to avoid redundant loads.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from ppt_agent.skills.registry import SkillMetadata, discover_skills

logger = logging.getLogger(__name__)


class SkillLoader:
    """Progressive skill loader.

    Maintains a set of already-loaded skills to enforce single-load guarantee.
    """

    def __init__(self, root: Path = Path(".catpaw/skills")) -> None:
        self.root = root
        self._loaded: set[str] = set()
        self._catalog: list[SkillMetadata] | None = None

    def get_catalog(self) -> list[SkillMetadata]:
        """Phase 1: Return a lightweight inventory of all available skills.

        This is cheap — only reads directory names and SKILL.md frontmatter
        for the description. Used to build the system prompt skill inventory.
        """
        if self._catalog is None:
            self._catalog = _discover_with_descriptions(self.root)
        return self._catalog

    def get_catalog_summary(self) -> str:
        """Return a compact skill inventory for injection into system prompt."""
        catalog = self.get_catalog()
        if not catalog:
            return ""
        lines = ["## Available Skills\n"]
        for skill in catalog:
            desc = skill.description or "(no description)"
            lines.append(f"- **{skill.name}**: {desc}")
        return "\n".join(lines)

    def load_skill(self, skill_name: str) -> str | None:
        """Phase 2: Load full SKILL.md content for a specific skill.

        Returns the full content on first load, or None if already loaded
        (single-load guarantee). Returns None if skill not found.
        """
        if skill_name in self._loaded:
            logger.debug("Skill '%s' already loaded, skipping.", skill_name)
            return None

        path = self.root / skill_name / "SKILL.md"
        if not path.exists():
            logger.warning("Skill '%s' not found at %s", skill_name, path)
            return None

        content = path.read_text(encoding="utf-8")
        self._loaded.add(skill_name)
        logger.info("Loaded skill: %s (%d chars)", skill_name, len(content))
        return content

    def load_skill_examples(self, skill_name: str) -> list[dict]:
        """Load example JSON files from a skill's examples/ directory."""
        examples_dir = self.root / skill_name / "examples"
        if not examples_dir.exists():
            return []
        results = []
        import json
        for example_file in sorted(examples_dir.glob("*.json")):
            try:
                data = json.loads(example_file.read_text(encoding="utf-8"))
                results.append({"filename": example_file.name, "data": data})
            except Exception:
                continue
        return results

    @property
    def loaded_skills(self) -> set[str]:
        return self._loaded.copy()


def _discover_with_descriptions(root: Path) -> list[SkillMetadata]:
    """Discover skills and extract descriptions from SKILL.md frontmatter."""
    result = []
    if not root.exists():
        return result

    for path in sorted(root.iterdir()):
        skill_md = path / "SKILL.md"
        if not skill_md.exists():
            continue

        description = ""
        try:
            text = skill_md.read_text(encoding="utf-8")
            # Parse YAML frontmatter
            if text.startswith("---"):
                end = text.find("---", 3)
                if end > 0:
                    frontmatter = yaml.safe_load(text[3:end])
                    if isinstance(frontmatter, dict):
                        description = frontmatter.get("description", "")
        except Exception:
            pass

        result.append(SkillMetadata(name=path.name, path=skill_md, description=description))

    return result


# Legacy function for backward compatibility
def load_skill(skill_name: str, root: Path = Path(".catpaw/skills")) -> str:
    """Load a skill's SKILL.md content. Raises FileNotFoundError if missing."""
    path = root / skill_name / "SKILL.md"
    if not path.exists():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")
