from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def emit_event(job_root: Path, phase: str, event_type: str, message: str = "", artifact_path: str | None = None, metadata: dict | None = None) -> dict:
    event = {
        "event_id": f"evt_{uuid4().hex[:12]}",
        "job_id": Path(job_root).name,
        "phase": phase,
        "type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": message,
    }
    if artifact_path:
        event["artifact_path"] = artifact_path
    if metadata:
        event["metadata"] = metadata
    history = Path(job_root) / "history.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    with history.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event
