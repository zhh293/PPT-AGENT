from __future__ import annotations

from pathlib import Path

from ppt_agent.coordinator.phase_state import create_job
from ppt_agent.coordinator.workflow import run_workflow
from ppt_agent.models.artifacts import read_json
from ppt_agent.retrieval.template_index import load_template_index


def test_template_matching_uses_configurable_knowledge_base(tmp_path: Path) -> None:
    job = create_job(Path("tests/fixtures/sample_project/input"), tmp_path / "job")
    run_workflow(job.root, until="content-review", force=True)

    selected = read_json(job.artifact_path("selected_template"))
    template_meta = read_json(job.artifact_path("template_meta"))

    # Should match a valid template from the index (or fallback)
    valid_ids = {t["template_id"] for t in load_template_index()}
    assert selected["template_id"] in valid_ids
    assert selected["ranking"]
    assert selected["ranking"][0]["template_id"] in valid_ids
    assert template_meta["template_id"] == selected["template_id"]
    assert template_meta["slide_count"] > 0
