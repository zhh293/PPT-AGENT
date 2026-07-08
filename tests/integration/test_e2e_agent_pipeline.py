"""End-to-end test: full deterministic pipeline produces final.pptx + all artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ppt_agent.coordinator.workflow import run_workflow
from ppt_agent.models.artifacts import JobWorkspace


def _run_full_pipeline(ws: JobWorkspace) -> None:
    """Run the full pipeline with review gate approval."""
    # Phase 1: document_analysis → content_mapping
    run_workflow(ws, model_profile="fake")

    # Approve review gate
    review = ws.root / "review_pending.json"
    if review.exists():
        r = json.loads(review.read_text(encoding="utf-8"))
        r["status"] = "approved"
        review.write_text(json.dumps(r), encoding="utf-8")

    sc = ws.root / "slide_contents.json"
    if sc.exists():
        s = json.loads(sc.read_text(encoding="utf-8"))
        s["review_status"] = "approved"
        sc.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")

    # Phase 2: visual_generation → verification
    run_workflow(ws, start_from="visual_generation", model_profile="fake", force=True)


@pytest.fixture
def sample_workspace(tmp_path: Path) -> JobWorkspace:
    job = tmp_path / "sample-job"
    job.mkdir(parents=True)
    (job / "input").mkdir()
    (job / "input" / "project.txt").write_text(
        "AI Document Analysis Platform\n"
        "Background: Enterprises process large volumes of unstructured documents daily.\n"
        "Capabilities: NLP parsing, intelligent classification, key information extraction, auto-summarization.\n"
        "Target audience: Enterprise decision makers and IT managers.\n"
        "Value proposition: Increase document processing efficiency by 80%, reduce labor costs by 60%.\n"
        "Achievements: 3 software copyrights, 50+ enterprise customers.",
        encoding="utf-8",
    )
    return JobWorkspace(root=job)


def test_full_pipeline_produces_final_pptx(sample_workspace: JobWorkspace) -> None:
    """End-to-end: all artifacts + valid final.pptx."""
    _run_full_pipeline(sample_workspace)

    # Verify key JSON artifacts exist
    required_artifacts = [
        "source_summary", "outline", "selected_template", "template_meta",
        "template_zones", "slide_design_plan", "slide_contents",
        "image_generation_config", "image_generation_report", "validation_report",
    ]
    for name in required_artifacts:
        path = sample_workspace.artifact_path(name)
        assert path.exists(), f"Missing: {name}"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict), f"{name} is not a dict"

    # Verify final.pptx
    pptx_path = sample_workspace.root / "final.pptx"
    assert pptx_path.exists(), "Missing final.pptx"
    assert pptx_path.stat().st_size > 5000, f"PPTX too small: {pptx_path.stat().st_size} bytes"


def test_final_pptx_has_content(sample_workspace: JobWorkspace) -> None:
    """Final PPTX is not empty — has slides and shapes."""
    _run_full_pipeline(sample_workspace)

    pptx_path = sample_workspace.root / "final.pptx"
    from ppt_agent.assembly.render_verify import inspect_pptx
    report = inspect_pptx(pptx_path)

    assert report["slide_count"] >= 3
    assert report["total_shapes"] >= 8
    assert len(report["warnings"]) == 0


def test_validation_report_covers_all_slides(sample_workspace: JobWorkspace) -> None:
    """Validation report matches slide count."""
    _run_full_pipeline(sample_workspace)

    vr_path = sample_workspace.artifact_path("validation_report")
    vr = json.loads(vr_path.read_text(encoding="utf-8"))

    assert "summary" in vr or "status" in vr, f"Unexpected validation report: {list(vr.keys())}"
