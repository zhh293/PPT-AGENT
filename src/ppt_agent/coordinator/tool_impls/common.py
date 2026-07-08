"""Common tools shared by all capabilities — read/write artifacts, validate, search KB, load skills."""

from __future__ import annotations

import json
from pathlib import Path

from ppt_agent.coordinator.phase_state import load_artifact, write_artifact
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.retrieval.query_router import query
from ppt_agent.runtime.agent_loop import ToolResult
from ppt_agent.tools.registry import FULL_ACCESS, WORKER_AND_ABOVE, ToolDescriptor


def make_read_artifact(workspace: JobWorkspace, **kwargs):
    def executor(args: dict) -> ToolResult:
        name = args.get("name", "")
        try:
            data = load_artifact(workspace, name) if name else {}
            return ToolResult(call_id="", output=data, success=True)
        except Exception as e:
            return ToolResult(call_id="", output=None, success=False, error=str(e))

    return ToolDescriptor("read_artifact", "artifact", FULL_ACCESS, "Read a JSON artifact from the workspace.",
                          {"name": {"type": "string", "description": "Artifact name"}}), executor


def make_write_artifact(workspace: JobWorkspace, **kwargs):
    def executor(args: dict) -> ToolResult:
        name = args.get("name", "")
        payload = args.get("payload", {})
        try:
            path = write_artifact(workspace, name, payload)
            return ToolResult(call_id="", output={"written": str(path)}, success=True)
        except Exception as e:
            return ToolResult(call_id="", output=None, success=False, error=str(e))

    return ToolDescriptor("write_artifact", "artifact", FULL_ACCESS, "Write a JSON artifact to the workspace.",
                          {"name": {"type": "string"}, "payload": {"type": "object"}}), executor


def make_validate_output(workspace: JobWorkspace, **kwargs):
    def executor(args: dict) -> ToolResult:
        name = args.get("name", "")
        path = workspace.artifact_path(name) if name else None
        if path and path.exists():
            return ToolResult(call_id="", output={"exists": True, "path": str(path), "size": path.stat().st_size}, success=True)
        return ToolResult(call_id="", output={"exists": False}, success=False, error=f"Artifact '{name}' not found")

    return ToolDescriptor("validate_output", "artifact", FULL_ACCESS, "Verify an artifact exists and is valid.",
                          {"name": {"type": "string"}}), executor


def make_search_knowledge_base(workspace: JobWorkspace, **kwargs):
    def executor(args: dict) -> ToolResult:
        q = args.get("query", "")
        top_k = int(args.get("top_k", 5))
        try:
            from ppt_agent.retrieval.template_index import load_template_index
            templates = load_template_index()
            items = [{"id": t["template_id"], "text": t.get("retrieval_text", ""), **t} for t in templates]
            ranked = query(items, q, ["bm25", "vector"], top_k)
            return ToolResult(call_id="", output={"results": ranked[:top_k]}, success=True)
        except Exception as e:
            return ToolResult(call_id="", output=None, success=False, error=str(e))

    return ToolDescriptor("search_knowledge_base", "retrieval", WORKER_AND_ABOVE,
                          "Search the knowledge base using hybrid retrieval (BM25 + vector + RRF).",
                          {"query": {"type": "string"}, "top_k": {"type": "integer", "default": 5}}), executor


def make_load_skill(workspace: JobWorkspace, **kwargs):
    """Returns a tool descriptor only — the executor is wired in by the AgentLoop."""
    return ToolDescriptor("load_skill", "skill", WORKER_AND_ABOVE,
                          "Load a skill's SKILL.md content into the agent context.",
                          {"skill_name": {"type": "string"}}), None
