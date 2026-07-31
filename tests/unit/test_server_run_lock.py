from pathlib import Path

import json
import os

import ppt_agent.server.app as server_app
from ppt_agent.server.app import (
    PHASES,
    _acquire_job_run_lock,
    _get_job_status,
    _release_job_run_lock,
)


def test_job_run_lock_is_atomic_and_reusable(tmp_path: Path) -> None:
    first = _acquire_job_run_lock(tmp_path)
    second = _acquire_job_run_lock(tmp_path)

    assert first is True
    assert second is False

    _release_job_run_lock(tmp_path)


def test_job_run_lock_recovers_dead_legacy_pid(
    tmp_path: Path,
    monkeypatch,
) -> None:
    lock_path = tmp_path / ".run.lock"
    lock_path.write_text("999999", encoding="utf-8")
    monkeypatch.setattr(server_app, "_pid_is_running", lambda _pid: False)

    assert _acquire_job_run_lock(tmp_path) is True

    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["pid"] == os.getpid()
    assert payload["job_id"] == tmp_path.name
    assert payload["run_id"]
    _release_job_run_lock(tmp_path)

    assert _acquire_job_run_lock(tmp_path) is True
    _release_job_run_lock(tmp_path)


def test_job_status_preserves_completed_with_fallbacks(tmp_path: Path) -> None:
    artifact_names = [
        "source_summary.json", "outline.json", "selected_template.json",
        "slide_design_plan.json", "slide_contents.json",
        "image_generation_report.json", "final.pptx", "validation_report.json",
    ]
    for name in artifact_names:
        (tmp_path / name).write_text("{}", encoding="utf-8")
    (tmp_path / "job.json").write_text(json.dumps({
        "status": "completed_with_fallbacks",
    }), encoding="utf-8")

    status = _get_job_status(tmp_path)

    assert len(status.phases_completed) == len(PHASES)
    assert status.status == "completed_with_fallbacks"
