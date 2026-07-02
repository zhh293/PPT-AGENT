from __future__ import annotations

import time
from pathlib import Path

from ppt_agent.coordinator.phase_state import create_job
from ppt_agent.coordinator.workflow import run_workflow
from ppt_agent.models.artifacts import atomic_write_json, read_json
from ppt_agent.models.slide_contents import approve_slide_contents


def test_representative_job_stays_inside_local_mvp_budget(tmp_path: Path) -> None:
    job = create_job(Path("tests/fixtures/sample_project/input"), tmp_path / "perf")

    started = time.perf_counter()
    run_workflow(job.root, until="content-review", force=True)
    content_review_seconds = time.perf_counter() - started

    payload = read_json(job.artifact_path("slide_contents"))
    atomic_write_json(job.artifact_path("slide_contents"), approve_slide_contents(payload))

    started = time.perf_counter()
    run_workflow(job.root, start_from="visual-generation", force=True)
    assembly_seconds = time.perf_counter() - started

    assert len(payload["slides"]) >= 8
    assert content_review_seconds < 30
    assert assembly_seconds < 90
