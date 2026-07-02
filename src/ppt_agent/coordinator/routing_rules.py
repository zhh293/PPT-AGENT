from __future__ import annotations

from pathlib import Path

import yaml


def load_routes(path: Path = Path("config/dispatcher.yml")) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("routes", data)
