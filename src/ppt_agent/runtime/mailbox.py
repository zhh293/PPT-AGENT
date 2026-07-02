from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class Mailbox:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def append(self, sender: str, recipient: str, message: str, artifact_refs: list[str] | None = None) -> dict:
        item = {
            "sender": sender,
            "recipient": recipient,
            "message": message,
            "artifact_refs": artifact_refs or [],
            "read": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        return item

    def read_all(self) -> list[dict]:
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
