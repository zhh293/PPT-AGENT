from __future__ import annotations

from pathlib import Path

from ppt_agent.coordinator.phase_state import load_artifact, write_artifact
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.models.selected_template import fallback_selection
from ppt_agent.models.template_meta import default_template_meta
from ppt_agent.retrieval.query_router import query
from ppt_agent.retrieval.template_index import load_template_index


def run(workspace: JobWorkspace, force: bool = False) -> list[Path]:
    selected_path = workspace.artifact_path("selected_template")
    meta_path = workspace.artifact_path("template_meta")
    if selected_path.exists() and meta_path.exists() and not force:
        return [selected_path, meta_path]
    outline = load_artifact(workspace, "outline")
    slide_count = outline["meta"]["total_slides"]
    templates = load_template_index()
    query_text = " ".join([outline["meta"].get("domain", ""), outline["meta"].get("audience", ""), outline["meta"].get("tone", "")])
    ranked = query(
        [{"id": item["template_id"], "template_id": item["template_id"], "text": item.get("retrieval_text", ""), **item} for item in templates],
        query_text,
        ["bm25"],
        3,
    )
    selected = fallback_selection()
    if ranked and ranked[0].get("score", 0) > 0:
        selected["ranking"] = [
            {
                "template_id": item["template_id"],
                "score": min(1.0, float(item.get("score", 0))),
                "rank": rank,
                "domain_fit": 0.6,
                "layout_fit": 0.7,
                "tone_fit": 0.6,
                "notes": "Ranked by local BM25 retrieval.",
            }
            for rank, item in enumerate(ranked, start=1)
        ]
    meta = default_template_meta(slide_count)
    return [write_artifact(workspace, "selected_template", selected), write_artifact(workspace, "template_meta", meta)]
