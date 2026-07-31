from __future__ import annotations

import subprocess
import json

from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.workers.image_generator import (
    _invoke_gptimage2_skill_background,
    _resolve_gptimage2_accounts_file,
    check_generated_images,
)
from ppt_agent.workers.ppt_verifier import run as run_ppt_verifier


def test_check_generated_images_uses_normalized_fallback_status(tmp_path) -> None:
    workspace = JobWorkspace(tmp_path / "job")
    workspace.ensure()

    report = check_generated_images(
        workspace,
        {"slides": [{"index": 0, "output_name": "slide-000.png"}]},
    )

    assert report["status"] == "succeeded_with_fallbacks"
    assert report["generated"] == 0
    assert report["fallback"] == 1


def test_accounts_file_prefers_explicit_environment_path(
    tmp_path, monkeypatch
) -> None:
    workspace = JobWorkspace(tmp_path / "job")
    workspace.ensure()
    configured = tmp_path / "private-accounts.json"
    monkeypatch.setenv(
        "PPT_AGENT_GPTIMAGE2_ACCOUNTS_FILE",
        str(configured),
    )

    assert _resolve_gptimage2_accounts_file(workspace) == configured


def test_accounts_file_reuses_project_skill_pool(tmp_path, monkeypatch) -> None:
    workspace = JobWorkspace(tmp_path / "job")
    workspace.ensure()
    monkeypatch.delenv("PPT_AGENT_GPTIMAGE2_ACCOUNTS_FILE", raising=False)

    resolved = _resolve_gptimage2_accounts_file(workspace)

    assert resolved.name == "accounts.json"
    assert resolved.parent.name == "assets"
    assert resolved.is_file()


def test_accounts_file_falls_back_to_job_store_without_shared_pool(
    tmp_path, monkeypatch
) -> None:
    import ppt_agent.workers.image_generator as image_generator

    workspace = JobWorkspace(tmp_path / "job")
    workspace.ensure()
    monkeypatch.delenv("PPT_AGENT_GPTIMAGE2_ACCOUNTS_FILE", raising=False)
    monkeypatch.setattr(image_generator, "_PROJECT_ROOT", tmp_path / "empty-project")

    assert image_generator._resolve_gptimage2_accounts_file(workspace) == (
        workspace.root / ".gptimage2_accounts.json"
    )


def test_background_launcher_does_not_lock_workspace_log(tmp_path, monkeypatch) -> None:
    workspace = JobWorkspace(tmp_path / "job")
    workspace.ensure()
    captured = {}

    class FakeProcess:
        pid = 4321

    def fake_popen(*args, **kwargs):
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    pid = _invoke_gptimage2_skill_background(workspace, {"slides": []})

    assert pid == 4321
    assert captured["stdout"] == subprocess.DEVNULL
    assert captured["stderr"] == subprocess.DEVNULL
    assert not (workspace.root / "gptimage2_output.log").exists()


def test_verifier_fails_when_visual_generation_produces_no_images(tmp_path) -> None:
    workspace = JobWorkspace(tmp_path / "job")
    workspace.ensure()
    workspace.artifact_path("slide_contents").write_text(
        json.dumps(
            {
                "slides": [
                    {
                        "slide_index": 0,
                        "zones": [
                            {
                                "type": "title",
                                "content": "Demo",
                                "editable": True,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    workspace.artifact_path("image_generation_report").write_text(
        json.dumps(
            {
                "status": "succeeded_with_fallbacks",
                "total_slides": 1,
                "generated": 0,
                "fallback": 1,
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    workspace.artifact_path("slide_design_plan").write_text(
        json.dumps(
            {
                "slides": [
                    {
                        "slide_index": 0,
                        "visual_strategy": "generated_image",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    output = run_ppt_verifier(workspace, force=True)
    report = json.loads(output.read_text(encoding="utf-8"))

    assert report["status"] == "failed"
    assert any(
        "no usable slide backgrounds" in item
        for item in report["manual_review_items"]
    )
