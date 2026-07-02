from __future__ import annotations

import os
from pathlib import Path


class SafeFilesystem:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    def resolve(self, path: str | Path) -> Path:
        candidate = (self.root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        if self.root != candidate and self.root not in candidate.parents:
            raise PermissionError(f"Path escapes sandbox: {candidate}")
        return candidate

    def atomic_write_text(self, path: str | Path, content: str) -> Path:
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
        return target
