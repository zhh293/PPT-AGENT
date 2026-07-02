from __future__ import annotations

import json
from pathlib import Path


def test_all_schema_files_parse() -> None:
    for path in Path("specs/001-ppt-generation-agent/contracts").glob("*.schema.json"):
        assert json.loads(path.read_text(encoding="utf-8"))["$schema"]
