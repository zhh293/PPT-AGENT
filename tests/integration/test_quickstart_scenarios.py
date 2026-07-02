from __future__ import annotations

from pathlib import Path

from ppt_agent.coordinator.phase_state import create_job
from ppt_agent.coordinator.workflow import run_workflow
from ppt_agent.models.artifacts import atomic_write_json, read_json
from ppt_agent.models.slide_contents import approve_slide_contents


def _prepared_job(tmp_path: Path, name: str):
    job = create_job(Path("tests/fixtures/sample_project/input"), tmp_path / name)
    run_workflow(job.root, until="content-review", force=True)
    return job


def test_quickstart_full_draft_with_editable_text(tmp_path: Path) -> None:
    job = _prepared_job(tmp_path, "full")
    atomic_write_json(job.artifact_path("slide_contents"), approve_slide_contents(read_json(job.artifact_path("slide_contents"))))
    run_workflow(job.root, start_from="visual-generation", force=True)
    report = read_json(job.artifact_path("validation_report"))
    assert (job.root / "final.pptx").exists()
    assert all(slide["checks"]["text_editable"] for slide in report["slide_results"])


def test_quickstart_no_visual_generation_account(tmp_path: Path) -> None:
    job = _prepared_job(tmp_path, "fallback")
    atomic_write_json(job.artifact_path("slide_contents"), approve_slide_contents(read_json(job.artifact_path("slide_contents"))))
    run_workflow(job.root, start_from="visual-generation", force=True)
    report = read_json(job.artifact_path("image_generation_report"))
    assert report["status"] == "succeeded_with_fallbacks"


def test_quickstart_long_text_overflow_warning(tmp_path: Path) -> None:
    job = _prepared_job(tmp_path, "long-text")
    payload = read_json(job.artifact_path("slide_contents"))
    for zone in payload["slides"][0]["zones"]:
        if zone["type"] == "title":
            zone["content"] = "超长标题" * 40
    atomic_write_json(job.artifact_path("slide_contents"), approve_slide_contents(payload))
    run_workflow(job.root, start_from="visual-generation", force=True)
    assert read_json(job.artifact_path("validation_report"))["status"] == "passed_with_warnings"


def test_quickstart_template_mismatch_falls_back_to_reusable_layouts(tmp_path: Path) -> None:
    job = _prepared_job(tmp_path, "template-mismatch")
    template_meta = read_json(job.artifact_path("template_meta"))
    template_meta["slides"] = template_meta["slides"][:1]
    atomic_write_json(job.artifact_path("template_meta"), template_meta)
    atomic_write_json(job.artifact_path("slide_contents"), approve_slide_contents(read_json(job.artifact_path("slide_contents"))))
    run_workflow(job.root, start_from="visual-generation", force=True)
    assert (job.root / "final.pptx").exists()
    assert read_json(job.artifact_path("validation_report"))["status"] in {"passed", "passed_with_warnings"}
