from __future__ import annotations

from pathlib import Path


def ocr_image(path: Path) -> tuple[str, list[str]]:
    return "", [f"OCR unavailable for {path.name}; image retained for visual inventory."]
