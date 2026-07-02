from __future__ import annotations

import json
from pathlib import Path


def load_template_index(path: Path = Path("templates/index.json")) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return data.get("templates", [])
    return data
