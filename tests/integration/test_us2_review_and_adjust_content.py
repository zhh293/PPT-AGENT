from __future__ import annotations

import zipfile
from pathlib import Path

from ppt_agent.coordinator.phase_state import create_job
from ppt_agent.coordinator.workflow import run_workflow
from ppt_agent.models.artifacts import atomic_write_json, read_json
from ppt_agent.models.slide_contents import approve_slide_contents


def test_edit_approve_and_assemble_preserves_text(tmp_path: Path) -> None:
    job = create_job(Path("tests/fixtures/sample_project/input"), tmp_path / "job")
    run_workflow(job.root, until="content-review", force=True)
    payload = read_json(job.artifact_path("slide_contents"))
    payload["slides"][0]["zones"][0]["content"] = "用户修改后的标题"
    payload["slides"][0]["review_status"] = "edited"
    atomic_write_json(job.artifact_path("slide_contents"), approve_slide_contents(payload))
    run_workflow(job.root, start_from="visual-generation", force=True)
    with zipfile.ZipFile(job.root / "final.pptx") as zf:
        assert "用户修改后的标题" in zf.read("ppt/slides/slide1.xml").decode("utf-8")
