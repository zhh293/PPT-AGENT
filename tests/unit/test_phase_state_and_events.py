from __future__ import annotations

from pathlib import Path

from ppt_agent.coordinator.event_bus import emit_event
from ppt_agent.coordinator.phase_state import create_job


def test_create_job_and_event(tmp_path: Path) -> None:
    src = tmp_path / "input"
    src.mkdir()
    (src / "plan.md").write_text("# Demo", encoding="utf-8")
    job = create_job(src, tmp_path / "job")
    assert (job.input_dir / "plan.md").exists()
    emit_event(job.root, "document_analysis", "started", "ok")
    assert job.history_path.read_text(encoding="utf-8")
