from __future__ import annotations

import zipfile
from pathlib import Path

from ppt_agent.coordinator.phase_state import create_job
from ppt_agent.coordinator.workflow import run_workflow
from ppt_agent.models.artifacts import read_json
from ppt_agent.models.slide_contents import approve_slide_contents
from ppt_agent.models.artifacts import atomic_write_json


def test_us1_generate_editable_presentation(tmp_path: Path) -> None:
    src = Path("tests/fixtures/sample_project/input")
    job = create_job(src, tmp_path / "sample-project")
    run_workflow(job.root, until="content-review", force=True)
    for name in ["source_summary", "outline", "selected_template", "template_meta", "slide_design_plan", "slide_contents"]:
        assert job.artifact_path(name).exists()
    contents = approve_slide_contents(read_json(job.artifact_path("slide_contents")))
    atomic_write_json(job.artifact_path("slide_contents"), contents)
    run_workflow(job.root, start_from="visual-generation", force=True)
    assert (job.root / "final.pptx").exists()
    assert job.artifact_path("validation_report").exists()
    with zipfile.ZipFile(job.root / "final.pptx") as zf:
        slide_xml = zf.read("ppt/slides/slide1.xml").decode("utf-8")
    assert "智能图书管理系统" in slide_xml
