from __future__ import annotations

from pathlib import Path

from ppt_agent.coordinator.phase_state import create_job
from ppt_agent.coordinator.workflow import run_workflow
from ppt_agent.models.artifacts import atomic_write_json, read_json
from ppt_agent.models.slide_contents import approve_slide_contents


def test_validation_report_flags_long_text_and_fallbacks(tmp_path: Path) -> None:
    job = create_job(Path("tests/fixtures/sample_project/input"), tmp_path / "job")
    run_workflow(job.root, until="content-review", force=True)
    payload = read_json(job.artifact_path("slide_contents"))
    for zone in payload["slides"][1]["zones"]:
        if zone["type"] == "bullets":
            zone["content"] = ["这是一条非常长的测试文本" * 20]
    atomic_write_json(job.artifact_path("slide_contents"), approve_slide_contents(payload))

    run_workflow(job.root, start_from="visual-generation", force=True)

    report = read_json(job.artifact_path("validation_report"))
    assert report["status"] == "passed_with_warnings"
    assert any("text fit" in issue.lower() or "overflow" in issue.lower() for slide in report["slide_results"] for issue in slide["issues"])
    assert report["manual_review_items"]
