from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

from ppt_agent.models.artifacts import JobWorkspace, atomic_write_json, read_json


def create_job(input_path: Path, output_path: Path | None = None) -> JobWorkspace:
    source = Path(input_path).resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    job_root = Path(output_path).resolve() if output_path else Path("workspace/jobs") / f"job-{uuid4().hex[:8]}"
    workspace = JobWorkspace(job_root)
    workspace.ensure()
    if source.is_dir():
        for item in source.iterdir():
            dest = workspace.input_dir / item.name
            if item.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
    else:
        shutil.copy2(source, workspace.input_dir / source.name)
    atomic_write_json(
        workspace.root / "job.json",
        {"job_id": workspace.root.name, "input_dir": str(workspace.input_dir), "status": "created"},
    )
    return workspace


def write_artifact(workspace: JobWorkspace, name: str, payload: dict) -> Path:
    path = workspace.artifact_path(name)
    atomic_write_json(path, payload)
    return path


def load_artifact(workspace: JobWorkspace, name: str) -> dict:
    return read_json(workspace.artifact_path(name))


def update_job_status(workspace: JobWorkspace, status: str) -> None:
    meta_path = workspace.root / "job.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"job_id": workspace.root.name}
    meta["status"] = status
    atomic_write_json(meta_path, meta)
