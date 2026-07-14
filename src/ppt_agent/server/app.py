"""FastAPI application for PPT Agent dashboard.

Provides:
- SSE endpoint for real-time event streaming
- REST APIs for job management and artifact viewing
- File upload for creating new jobs
- Static file serving for the frontend
"""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Form, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ppt_agent.coordinator.event_bus import EventBus, EventConsumer, CallbackConsumer
from ppt_agent.coordinator.phase_state import create_job
from ppt_agent.coordinator.workflow import PHASES, run_workflow
from ppt_agent.models.artifacts import JobWorkspace, read_json

# ─── Configuration ─────────────────────────────────────────────────────
WORKSPACE_ROOT = Path("workspace/jobs")

# ─── App Setup ─────────────────────────────────────────────────────────
app = FastAPI(title="PPT Agent Dashboard", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── SSE Event Store ───────────────────────────────────────────────────
# In-memory event queues per job, subscribers get async queue
_event_subscribers: dict[str, list[asyncio.Queue]] = {}
_lock = threading.Lock()


def _broadcast_event(job_id: str, event: dict) -> None:
    """Broadcast event to all SSE subscribers of a job."""
    with _lock:
        queues = _event_subscribers.get(job_id, [])
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # Drop if subscriber is too slow


class SSEConsumer(EventConsumer):
    """EventBus consumer that broadcasts to SSE subscribers."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id

    def handle(self, event: dict) -> None:
        _broadcast_event(self.job_id, event)


# ─── Models ────────────────────────────────────────────────────────────
class JobInfo(BaseModel):
    job_id: str
    status: str
    phases_completed: list[str]
    current_phase: str | None
    created_at: str | None


class RunRequest(BaseModel):
    model_profile: str | None = None
    force: bool = False
    until: str | None = None
    start_from: str | None = None


# ─── Helper Functions ──────────────────────────────────────────────────
def _get_workspace_root() -> Path:
    """Get the absolute workspace root."""
    # Try relative to CWD first, then absolute
    if WORKSPACE_ROOT.exists():
        return WORKSPACE_ROOT
    abs_path = Path(__file__).parent.parent.parent.parent / "workspace" / "jobs"
    return abs_path


def _get_job_path(job_id: str) -> Path:
    path = _get_workspace_root() / job_id
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return path


def _get_job_status(job_path: Path) -> JobInfo:
    """Read job status from workspace artifacts."""
    job_id = job_path.name

    # Determine completed phases by checking artifact existence
    artifact_map = {
        "document_analysis": "source_summary.json",
        "outline_generation": "outline.json",
        "template_matching": "selected_template.json",
        "design_planning": "slide_design_plan.json",
        "content_mapping": "slide_contents.json",
        "visual_generation": "image_generation_report.json",
        "ppt_assembly": "final.pptx",
        "verification": "validation_report.json",
    }

    completed = []
    for phase, artifact in artifact_map.items():
        if (job_path / artifact).exists():
            completed.append(phase)

    # Determine current phase
    current = None
    status = "idle"
    if completed:
        if len(completed) == len(PHASES):
            status = "completed"
        else:
            status = "running"
            for p in PHASES:
                if p not in completed:
                    current = p
                    break

    # Try to read job.json for created_at
    created_at = None
    job_json = job_path / "job.json"
    if job_json.exists():
        try:
            data = json.loads(job_json.read_text())
            created_at = data.get("created_at")
        except Exception:
            pass

    return JobInfo(
        job_id=job_id,
        status=status,
        phases_completed=completed,
        current_phase=current,
        created_at=created_at,
    )


# ─── API Routes ────────────────────────────────────────────────────────

@app.get("/api/jobs")
def list_jobs() -> list[JobInfo]:
    """List all jobs in the workspace."""
    root = _get_workspace_root()
    if not root.exists():
        return []
    jobs = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            jobs.append(_get_job_status(child))
    return jobs


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> JobInfo:
    """Get status of a specific job."""
    path = _get_job_path(job_id)
    return _get_job_status(path)


@app.post("/api/jobs")
async def create_new_job(
    files: list[UploadFile] = File(...),
    user_prompt: str = Form(""),
) -> JobInfo:
    """Create a new job by uploading input files."""
    import tempfile
    from datetime import datetime, timezone

    # Create temp dir for uploaded files
    root = _get_workspace_root()
    root.mkdir(parents=True, exist_ok=True)

    # Generate job ID
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    job_id = f"job-{timestamp}"
    job_path = root / job_id
    input_dir = job_path / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    # Save uploaded files
    for f in files:
        dest = input_dir / f.filename
        content = await f.read()
        dest.write_bytes(content)

    # Create job.json
    job_meta = {
        "job_id": job_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_files": [f.filename for f in files],
        "status": "created",
        "user_prompt": user_prompt.strip() if user_prompt else "",
    }
    (job_path / "job.json").write_text(json.dumps(job_meta, indent=2))

    # Create required subdirs
    (job_path / "generated_slides").mkdir(exist_ok=True)
    (job_path / "layer_analysis").mkdir(exist_ok=True)

    return _get_job_status(job_path)


@app.post("/api/jobs/{job_id}/run")
async def run_job(job_id: str, req: RunRequest, background_tasks: BackgroundTasks) -> dict:
    """Start running a job in the background."""
    job_path = _get_job_path(job_id)

    def _run():
        try:
            run_workflow(
                job_path,
                until=req.until,
                start_from=req.start_from,
                force=req.force,
                model_profile=req.model_profile if req.model_profile is not None else "deepseek",
            )
            job_json = job_path / "job.json"
            meta = json.loads(job_json.read_text()) if job_json.exists() else {}
            meta["status"] = "completed"
            job_json.write_text(json.dumps(meta, indent=2))
        except Exception as e:
            job_json = job_path / "job.json"
            meta = json.loads(job_json.read_text()) if job_json.exists() else {}
            meta["status"] = "failed"
            meta["error"] = str(e)
            job_json.write_text(json.dumps(meta, indent=2))

    background_tasks.add_task(_run)
    return {"status": "started", "job_id": job_id}


@app.get("/api/jobs/{job_id}/artifacts")
def list_artifacts(job_id: str) -> list[dict]:
    """List all artifacts for a job."""
    job_path = _get_job_path(job_id)

    artifacts = []
    artifact_files = [
        ("source_summary", "source_summary.json", "document_analysis"),
        ("outline", "outline.json", "outline_generation"),
        ("selected_template", "selected_template.json", "template_matching"),
        ("template_meta", "template_meta.json", "template_matching"),
        ("slide_design_plan", "slide_design_plan.json", "design_planning"),
        ("slide_contents", "slide_contents.json", "content_mapping"),
        ("image_generation_config", "image_generation_config.json", "visual_generation"),
        ("image_generation_report", "image_generation_report.json", "visual_generation"),
        ("validation_report", "validation_report.json", "verification"),
    ]

    for name, filename, phase in artifact_files:
        path = job_path / filename
        if path.exists():
            stat = path.stat()
            artifacts.append({
                "name": name,
                "filename": filename,
                "phase": phase,
                "size": stat.st_size,
                "modified": stat.st_mtime,
            })

    # Check for final.pptx
    pptx_path = job_path / "final.pptx"
    if pptx_path.exists():
        stat = pptx_path.stat()
        artifacts.append({
            "name": "final_pptx",
            "filename": "final.pptx",
            "phase": "ppt_assembly",
            "size": stat.st_size,
            "modified": stat.st_mtime,
        })

    return artifacts


@app.get("/api/jobs/{job_id}/artifacts/{artifact_name}", response_model=None)
def get_artifact(job_id: str, artifact_name: str):
    """Get a specific artifact content."""
    job_path = _get_job_path(job_id)

    # Map artifact names to filenames
    name_map = {
        "source_summary": "source_summary.json",
        "outline": "outline.json",
        "selected_template": "selected_template.json",
        "template_meta": "template_meta.json",
        "slide_design_plan": "slide_design_plan.json",
        "slide_contents": "slide_contents.json",
        "image_generation_config": "image_generation_config.json",
        "image_generation_report": "image_generation_report.json",
        "validation_report": "validation_report.json",
        "final_pptx": "final.pptx",
    }

    filename = name_map.get(artifact_name)
    if not filename:
        raise HTTPException(status_code=404, detail=f"Unknown artifact: {artifact_name}")

    path = job_path / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Artifact not found: {filename}")

    if filename.endswith(".pptx"):
        return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation", filename=filename)

    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/jobs/{job_id}/events")
def get_events(job_id: str, limit: int = 100) -> list[dict]:
    """Get recent events from history.jsonl."""
    job_path = _get_job_path(job_id)
    history = job_path / "history.jsonl"

    if not history.exists():
        return []

    events = []
    for line in history.read_text(encoding="utf-8").strip().split("\n"):
        if line.strip():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    # Return most recent events
    return events[-limit:]


@app.get("/api/jobs/{job_id}/generation-progress")
def get_generation_progress(job_id: str) -> dict:
    """Get real-time image generation progress from batch_report.json."""
    job_path = _get_job_path(job_id)
    report_path = job_path / "background_images" / "batch_report.json"
    if not report_path.exists():
        return {"progress": "idle", "done": 0, "total": 0, "success": 0, "failed": 0}
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
        return {
            "progress": data.get("progress", "?"),
            "done": data.get("done", 0),
            "total": data.get("total", 0),
            "success": data.get("success", 0),
            "failed": data.get("failed", 0),
            "details": data.get("details", {"success": [], "failed": []}),
        }
    except Exception:
        return {"progress": "loading", "done": 0, "total": 0, "success": 0, "failed": 0}


@app.get("/api/jobs/{job_id}/stream")
async def stream_events(job_id: str) -> StreamingResponse:
    """SSE endpoint for real-time event streaming."""
    _get_job_path(job_id)  # Validate job exists

    queue: asyncio.Queue = asyncio.Queue(maxsize=256)

    with _lock:
        if job_id not in _event_subscribers:
            _event_subscribers[job_id] = []
        _event_subscribers[job_id].append(queue)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # Send initial connection event
            yield f"data: {json.dumps({'type': 'connected', 'job_id': job_id})}\n\n"

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield f": keepalive\n\n"
        finally:
            with _lock:
                if job_id in _event_subscribers:
                    _event_subscribers[job_id].remove(queue)
                    if not _event_subscribers[job_id]:
                        del _event_subscribers[job_id]

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/jobs/{job_id}/preview")
def get_preview(job_id: str) -> FileResponse:
    """Get the HTML preview file."""
    job_path = _get_job_path(job_id)
    preview = job_path / "preview.html"
    if not preview.exists():
        raise HTTPException(status_code=404, detail="Preview not generated yet")
    return FileResponse(preview, media_type="text/html")


@app.post("/api/jobs/{job_id}/approve")
def approve_job(job_id: str) -> dict:
    """Approve slide_contents for a job."""
    job_path = _get_job_path(job_id)
    slide_path = job_path / "slide_contents.json"

    if not slide_path.exists():
        raise HTTPException(status_code=400, detail="slide_contents.json not found")

    from ppt_agent.models.slide_contents import approve_slide_contents
    from ppt_agent.models.artifacts import atomic_write_json

    payload = approve_slide_contents(read_json(slide_path))
    atomic_write_json(slide_path, payload)

    return {"status": "approved", "path": str(slide_path)}


# ─── Static Files (frontend) ──────────────────────────────────────────
# Mount after API routes so API takes precedence
_frontend_dist = Path(__file__).parent.parent.parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")


def start_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Start the server programmatically."""
    import uvicorn
    uvicorn.run(app, host=host, port=port)
