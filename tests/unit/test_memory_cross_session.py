"""Tests for Phase 4.1 — workspace-level memory sharing across jobs."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ppt_agent.context.memory_layers import (
    append_memory,
    build_memory_context,
    get_workspace_memory_root,
    read_memory,
)


# ── get_workspace_memory_root ────────────────────────────────────────

def test_workspace_memory_root_from_env(tmp_path: Path) -> None:
    os.environ["PPT_AGENT_MEMORY_ROOT"] = str(tmp_path)
    try:
        root = get_workspace_memory_root(Path("/fake/jobs/job1"))
        assert root == tmp_path / ".ppt_agent"
    finally:
        del os.environ["PPT_AGENT_MEMORY_ROOT"]


def test_workspace_memory_root_from_job_parent(tmp_path: Path) -> None:
    job_root = tmp_path / "jobs" / "job1"
    root = get_workspace_memory_root(job_root)
    assert root == (tmp_path / "jobs" / ".ppt_agent")


# ── Cross-job memory sharing ─────────────────────────────────────────

def test_memory_persists_across_jobs(tmp_path: Path) -> None:
    """Job A writes memory → Job B can read it."""
    job_a = tmp_path / "jobs" / "job_a"
    job_b = tmp_path / "jobs" / "job_b"
    job_a.mkdir(parents=True)
    job_b.mkdir(parents=True)

    append_memory(job_a, "memory", "Insight from job A: Use blue templates for tech pitches.")

    # Job B should read the same memory (workspace-level)
    content_b = read_memory(job_b, "memory")
    assert "Insight from job A" in content_b


def test_session_still_per_job(tmp_path: Path) -> None:
    """Session.md is per-job, not shared."""
    job_a = tmp_path / "jobs" / "job_a"
    job_b = tmp_path / "jobs" / "job_b"
    job_a.mkdir(parents=True)
    job_b.mkdir(parents=True)

    append_memory(job_a, "session", "Job A phase 1 complete.")
    append_memory(job_b, "session", "Job B phase 1 complete.")

    sess_a = read_memory(job_a, "session")
    sess_b = read_memory(job_b, "session")
    assert "Job A" in sess_a
    assert "Job B" in sess_b
    assert "Job A" not in sess_b


def test_agent_is_immutable(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="immutable"):
        append_memory(tmp_path, "agent", "new content")


# ── Legacy path fallback ────────────────────────────────────────────

def test_read_memory_falls_back_to_legacy_path(tmp_path: Path) -> None:
    """Old per-job memory.md is still readable."""
    job = tmp_path / "jobs" / "old_job"
    job.mkdir(parents=True, exist_ok=True)

    # Write to the legacy per-job path
    legacy_dir = job / ".ppt_agent"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "memory.md").write_text("Legacy memory content.", encoding="utf-8")

    # Reading from the job root should find the legacy file
    content = read_memory(job, "memory")
    assert "Legacy memory content" in content


def test_workspace_memory_preferred_over_legacy(tmp_path: Path) -> None:
    """When both exist, workspace-level takes priority."""
    job = tmp_path / "jobs" / "job1"
    job.mkdir(parents=True)

    # Legacy per-job path
    legacy_dir = job / ".ppt_agent"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "memory.md").write_text("Old per-job memory.", encoding="utf-8")

    # Workspace-level path
    ws_root = get_workspace_memory_root(job)
    ws_root.mkdir(parents=True, exist_ok=True)
    (ws_root / "memory.md").write_text("New workspace memory.", encoding="utf-8")

    content = read_memory(job, "memory")
    assert "New workspace memory" in content
    assert "Old per-job memory" not in content


# ── build_memory_context ────────────────────────────────────────────

def test_build_memory_context_includes_shared_memory(tmp_path: Path) -> None:
    job = tmp_path / "jobs" / "job1"
    job.mkdir(parents=True, exist_ok=True)

    append_memory(job, "memory", "Shared knowledge: always use 16:9.")

    ctx = build_memory_context(job)
    assert "Shared knowledge" in ctx
    assert "16:9" in ctx
