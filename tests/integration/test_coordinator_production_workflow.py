from __future__ import annotations

import json
from pathlib import Path

from pptx import Presentation

from ppt_agent.coordinator.phase_state import create_job
from ppt_agent.coordinator.workflow import run_workflow


def test_coordinator_mode_recovers_and_delivers_pptx(tmp_path, monkeypatch) -> None:
    """A coordinator/model early exit must not sacrifice the PPT deliverable."""
    from ppt_agent.coordinator import workflow

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "project.md").write_text(
        "# Atlas AI\n\n"
        "Atlas AI turns field-service records into evidence-backed maintenance plans.\n\n"
        "- Reduces manual triage time\n"
        "- Keeps approved presentation text editable\n"
        "- Supports offline delivery when image generation is unavailable\n",
        encoding="utf-8",
    )
    workspace = create_job(inputs, tmp_path / "job")

    # The fake Coordinator intentionally returns done immediately. This
    # exercises the production recovery scheduler. Image generation is kept
    # offline so the test is deterministic and proves fallback delivery.
    monkeypatch.setitem(workflow._PHASE_EXTRA_KWARGS, "visual_generation", {"mode": "none"})
    from ppt_agent.llm.client import LLMClient
    fake_client = LLMClient.from_config(profile="fake", job_root=workspace.root)
    monkeypatch.setattr(workflow, "_create_llm_client", lambda profile, job_root: fake_client)

    outputs = run_workflow(
        workspace,
        model_profile="coordinator:fake",
        force=True,
    )

    final_pptx = workspace.root / "final.pptx"
    validation = workspace.artifact_path("validation_report")
    state = json.loads((workspace.root / "workflow_state.json").read_text(encoding="utf-8"))

    assert final_pptx in outputs
    assert final_pptx.exists() and final_pptx.stat().st_size > 0
    assert validation.exists()
    assert len(Presentation(final_pptx).slides) > 0
    assert state["status"] == "completed_with_recovery"
    assert state["recovery_mode"] == "llm_augmented_production_scheduler"
    assert (workspace.root / "dispatch_decisions.jsonl").exists() is False
