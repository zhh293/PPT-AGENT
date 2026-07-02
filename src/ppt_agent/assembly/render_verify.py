from __future__ import annotations

from pathlib import Path


def inspect_pptx(path: Path) -> dict:
    return {"exists": path.exists(), "size": path.stat().st_size if path.exists() else 0}
