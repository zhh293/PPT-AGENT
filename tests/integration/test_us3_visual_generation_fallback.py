from __future__ import annotations

from pathlib import Path

from ppt_agent.coordinator.phase_state import create_job
from ppt_agent.coordinator.workflow import run_workflow
from ppt_agent.models.artifacts import atomic_write_json, read_json
from ppt_agent.models.slide_contents import approve_slide_contents


def test_no_account_visual_fallback_mode(tmp_path: Path) -> None:
    job = create_job(Path("tests/fixtures/sample_project/input"), tmp_path / "job")
    run_workflow(job.root, until="content-review", force=True)
    atomic_write_json(job.artifact_path("slide_contents"), approve_slide_contents(read_json(job.artifact_path("slide_contents"))))
    run_workflow(job.root, start_from="visual-generation", force=True)
    report = read_json(job.artifact_path("image_generation_report"))
    assert report["status"] == "succeeded_with_fallbacks"
    assert job.artifact_path("image_generation_config").exists()
