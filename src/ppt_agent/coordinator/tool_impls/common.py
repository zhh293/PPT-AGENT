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
    def executor(call_id: str, arguments: dict) -> ToolResult:
        name = arguments.get("name", "")
        try:
            data = load_artifact(workspace, name) if name else {}
            return ToolResult(call_id=call_id, output=data, success=True)
        except Exception as e:
            return ToolResult(call_id=call_id, output=None, success=False, error=str(e))

    return ToolDescriptor("read_artifact", "artifact", FULL_ACCESS, "Read a JSON artifact from the workspace.",
                          {"name": {"type": "string", "description": "Artifact name"}}), executor


def make_write_artifact(workspace: JobWorkspace, **kwargs):
    def executor(call_id: str, arguments: dict) -> ToolResult:
        name = arguments.get("name", "")
        payload = arguments.get("payload", {})
        try:
            path = write_artifact(workspace, name, payload)
            return ToolResult(call_id=call_id, output={"written": str(path)}, success=True)
        except Exception as e:
            return ToolResult(call_id=call_id, output=None, success=False, error=str(e))

    return ToolDescriptor("write_artifact", "artifact", FULL_ACCESS, "Write a JSON artifact to the workspace.",
                          {"name": {"type": "string"}, "payload": {"type": "object"}}), executor


def make_validate_output(workspace: JobWorkspace, **kwargs):
    def executor(call_id: str, arguments: dict) -> ToolResult:
        name = arguments.get("name", "")
        path = workspace.artifact_path(name) if name else None
        if path and path.exists():
            return ToolResult(call_id=call_id, output={"exists": True, "path": str(path), "size": path.stat().st_size}, success=True)
        return ToolResult(call_id=call_id, output={"exists": False}, success=False, error=f"Artifact '{name}' not found")

    return ToolDescriptor("validate_output", "artifact", FULL_ACCESS, "Verify an artifact exists and is valid.",
                          {"name": {"type": "string"}}), executor


def make_search_knowledge_base(workspace: JobWorkspace, **kwargs):
    def executor(call_id: str, arguments: dict) -> ToolResult:
        q = arguments.get("query", "")
        top_k = int(arguments.get("top_k", 5))
        try:
            from ppt_agent.retrieval.template_index import load_template_chunks
            chunks = load_template_chunks()
            items = [{"id": c["chunk_id"], "text": c["text"], "template_id": c["template_id"],
                       "chunk_type": c["chunk_type"], "weight": c.get("weight", 0.33)} for c in chunks]
            ranked = query(items, q, top_k=min(top_k * 5, len(items)))

            # Aggregate by template_id with weights
            template_scores: dict[str, float] = {}
            for r in ranked:
                tid = r.get("template_id", "")
                if not tid: continue
                w = r.get("weight", 0.33)
                template_scores[tid] = template_scores.get(tid, 0.0) + r.get("score", 0) * w
            sorted_tpl = sorted(template_scores.items(), key=lambda x: -x[1])
            results = [{"template_id": tid, "score": round(s, 4)} for tid, s in sorted_tpl[:top_k]]

            return ToolResult(call_id=call_id, output={"results": results}, success=True)
        except Exception as e:
            return ToolResult(call_id=call_id, output=None, success=False, error=str(e))

    return ToolDescriptor("search_knowledge_base", "retrieval", WORKER_AND_ABOVE,
                          "Search the knowledge base using 3-layer chunking (overview+design+slides) with weighted aggregation.",
                          {"query": {"type": "string"}, "top_k": {"type": "integer", "default": 5}}), executor


def make_compose_artifact(workspace: JobWorkspace, **kwargs):
    def executor(call_id: str, arguments: dict) -> ToolResult:
        content = arguments.get("content", {})
        if not isinstance(content, dict):
            return ToolResult(call_id=call_id, output=None, success=False,
                              error="content must be a JSON object (dict)")
        if not content:
            return ToolResult(call_id=call_id, output=None, success=False,
                              error="content is empty — provide the full artifact data")
        return ToolResult(call_id=call_id, output={
            "composed": True,
            "keys": list(content.keys()),
            "size_estimate": len(json.dumps(content, ensure_ascii=False)),
            "data": content,
        }, success=True)

    return ToolDescriptor("compose_artifact", "artifact", FULL_ACCESS,
                          "Compose a JSON artifact from your reasoning. Provide the complete "
                          "content as a JSON object. The tool validates structure and returns "
                          "the data for you to review before writing.",
                          {"content": {"type": "object", "description": "The complete JSON object for the artifact"}}), executor


def make_load_skill(workspace: JobWorkspace, **kwargs):
    """Returns a tool descriptor only — the executor is wired in by the AgentLoop."""
    return ToolDescriptor("load_skill", "skill", WORKER_AND_ABOVE,
                          "Load a skill's SKILL.md content into the agent context.",
                          {"skill_name": {"type": "string"}}), None
