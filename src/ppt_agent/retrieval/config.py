from __future__ import annotations

from pathlib import Path

import yaml


def load_kb_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
