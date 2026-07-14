"""template_matching tools — search_templates (chunked RAG) + select_template (full worker)."""

import json
import logging

from ppt_agent.retrieval.query_router import query
from ppt_agent.retrieval.template_index import load_template_chunks
from ppt_agent.runtime.agent_loop import ToolResult
from ppt_agent.tools.registry import WORKER_AND_ABOVE, ToolDescriptor

logger = logging.getLogger(__name__)

# 3-layer chunk weights (matches template_matcher.py)
_CHUNK_WEIGHTS = {"overview": 0.4, "design": 0.2, "slides": 0.4}


def register_tools(registry, workspace, capability, llm_client=None):
    def executor(call_id: str, arguments: dict) -> ToolResult:
        q = arguments.get("query", "")
        top_k = int(arguments.get("top_k", 3))
        try:
            # ── 3-layer chunk retrieval ──
            chunks = load_template_chunks()
            chunk_docs = [
                {
                    "id": c["chunk_id"],
                    "template_id": c["template_id"],
                    "text": c["text"],
                    "chunk_type": c["chunk_type"],
                    "weight": c["weight"],
                }
                for c in chunks
            ]
            ranked_chunks = query(chunk_docs, q, top_k=max(30, len(chunk_docs)))

            # ── Weighted aggregation per template ──
            template_scores: dict[str, float] = {}
            template_hits: dict[str, list[dict]] = {}
            for chunk in ranked_chunks:
                tid = chunk.get("template_id", "")
                if not tid:
                    continue
                weight = chunk.get("weight", 0.33)
                score = chunk.get("score", 0.0)
                template_scores[tid] = template_scores.get(tid, 0.0) + score * weight
                if tid not in template_hits:
                    template_hits[tid] = []
                template_hits[tid].append(chunk)

            ranked_templates = sorted(template_scores.items(), key=lambda x: -x[1])

            # ── LLM re-ranking if available ──
            if llm_client is not None and len(ranked_templates) > 1:
                try:
                    ranked_templates = _llm_rerank(
                        llm_client, q, chunks, ranked_templates,
                    )
                except Exception as e:
                    logger.warning("LLM template rerank failed: %s", e)

            # Build results with chunk hit info
            results = []
            for rank, (tid, score) in enumerate(ranked_templates[:top_k], start=1):
                hits = template_hits.get(tid, [])
                results.append({
                    "template_id": tid,
                    "score": round(score, 4),
                    "rank": rank,
                    "chunks_hit": len(hits),
                    "chunk_types": [h.get("chunk_type", "?") for h in hits],
                })

            return ToolResult(call_id=call_id, output={"query": q, "results": results}, success=True)
        except Exception as e:
            return ToolResult(call_id=call_id, output=None, success=False, error=str(e))

    registry.register_tool(
        ToolDescriptor("search_templates", "retrieval", WORKER_AND_ABOVE,
                       "Search templates using 3-layer chunking (overview+design+slides) with weighted aggregation and optional LLM re-ranking.",
                       {"query": {"type": "string"}, "top_k": {"type": "integer", "default": 3}}),
        executor,
    )

    def select_template(call_id: str, arguments: dict) -> ToolResult:
        """Run template matching in deterministic mode — agent already evaluated candidates.

        Uses 3-layer chunking + BM25/vector + weighted aggregation. No LLM passed
        to worker so it skips the LLM rerank step (agent does that via search_templates).
        """
        try:
            from ppt_agent.workers.template_matcher import run as tm_run
            force = arguments.get("force", False)
            output = tm_run(workspace, force=force)
            paths = output if isinstance(output, list) else [output]
            return ToolResult(call_id=call_id, output={"paths": [str(p) for p in paths], "status": "ok"}, success=True)
        except Exception as e:
            return ToolResult(call_id=call_id, output=None, success=False, error=str(e))

    registry.register_tool(
        ToolDescriptor("select_template", "retrieval", WORKER_AND_ABOVE,
                       "Run the complete template matching pipeline: search, select best match, produce selected_template.json, template_meta.json, template_zones.json.",
                       {"force": {"type": "boolean", "default": False}}),
        select_template)


def _llm_rerank(llm_client, query_text, chunks, ranked):
    """LLM semantic re-ranking for agent mode."""
    # Build template summaries from chunks
    tpl_summaries = {}
    for c in chunks:
        tid = c["template_id"]
        if tid not in tpl_summaries:
            tpl_summaries[tid] = {}
        tpl_summaries[tid][c["chunk_type"]] = c["text"]

    candidates = ranked[:5]
    tpl_list = []
    for tid, score in candidates:
        info = tpl_summaries.get(tid, {})
        tpl_list.append({
            "id": tid,
            "retrieval_score": round(score, 4),
            "overview": info.get("overview", "")[:200],
            "slides_structure": info.get("slides", "")[:200],
        })

    prompt = (
        f"Query: {query_text}\n\n"
        f"Candidate templates (pre-ranked by keyword relevance):\n"
        f"{json.dumps(tpl_list, ensure_ascii=False, indent=2)}\n\n"
        "Select the best matching template considering domain, style, audience fit, and slide count. "
        "Return JSON: {\"rankings\": [{\"template_id\": \"...\", \"score\": 0.0-1.0, \"reason\": \"...\"}]}"
    )

    result = llm_client.generate_json(
        prompt=prompt, context={},
        system="You are a presentation design expert. Pick the best template.",
        phase="template_matching",
        fallback={"rankings": [{"template_id": tid, "score": 1.0 - i * 0.1, "reason": "fallback"}
                              for i, (tid, _) in enumerate(candidates)]},
    )

    llm_scored = [(r["template_id"], float(r["score"])) for r in result.get("rankings", [])]
    llm_scored.extend((tid, s * 0.5) for tid, s in ranked[5:])
    llm_scored.sort(key=lambda x: -x[1])
    return llm_scored
