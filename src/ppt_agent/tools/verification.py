from __future__ import annotations

from pathlib import Path


def verify_file_exists(path: Path) -> bool:
    return Path(path).exists() and Path(path).stat().st_size > 0
