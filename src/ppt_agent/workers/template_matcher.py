"""Template matching worker — select the best template for the project.

Reads the outline to understand domain/audience/tone, searches the
template index via BM25+TF-IDF, and produces:
    - selected_template.json  (which template was chosen and why)
    - template_meta.json      (full per-slide zone structure for downstream)
    - template_zones.json     (per-slide zone + image path mapping)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ppt_agent.coordinator.phase_state import load_artifact, write_artifact
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.models.template_meta import (
    build_template_meta_from_ingest,
    default_template_meta,
)
from ppt_agent.retrieval.query_router import query
from ppt_agent.retrieval.template_index import (
    get_slide_image_paths,
    get_slide_zones,
    load_template_chunks,
    load_template_meta,
    resolve_template_dir,
)

logger = logging.getLogger(__name__)

MIN_TEMPLATE_MATCH_SCORE = 0.30
MIN_TEMPLATE_MATCH_MARGIN = 0.03


def _has_confident_template_match(ranked_templates: list[tuple[str, float]]) -> bool:
    """Reject weak or effectively tied retrieval results."""
    if not ranked_templates or ranked_templates[0][1] < MIN_TEMPLATE_MATCH_SCORE:
        return False
    if len(ranked_templates) == 1:
        return True
    return (ranked_templates[0][1] - ranked_templates[1][1]) >= MIN_TEMPLATE_MATCH_MARGIN


def _choose_required_real_template(
    ranked_templates: list[tuple[str, float]],
    entries: list[dict],
) -> tuple[dict, float, list[tuple[str, float]]]:
    """Choose the best usable real template; never return the blank fallback."""
    usable_by_id: dict[str, dict] = {}
    for entry in entries:
        template_id = entry.get("template_id", "")
        template_path = entry.get("path", "")
        if not template_id or template_id == "fallback.default" or not template_path:
            continue
        pptx_path = Path("templates") / template_path / "template.pptx"
        if pptx_path.exists():
            usable_by_id[template_id] = entry

    usable_ranked = [
        (template_id, score)
        for template_id, score in ranked_templates
        if template_id in usable_by_id
    ]
    if usable_ranked:
        template_id, score = usable_ranked[0]
        return usable_by_id[template_id], score, usable_ranked

    # Retrieval can be empty for sparse/legacy metadata. The hard template
    # requirement still applies, so select the first deterministic usable
    # library entry and surface the zero-confidence risk.
    if usable_by_id:
        template_id = sorted(usable_by_id)[0]
        return usable_by_id[template_id], 0.0, [(template_id, 0.0)]

    raise RuntimeError(
        "A real PowerPoint template is required, but no indexed template with "
        "an existing template.pptx is available. Add/ingest a template before running."
    )


def run(workspace: JobWorkspace, force: bool = False, llm_client=None) -> list[Path]:
    selected_path = workspace.artifact_path("selected_template")
    meta_path = workspace.artifact_path("template_meta")
    zones_path = workspace.artifact_path("template_zones")
    if selected_path.exists() and meta_path.exists() and not force:
        return [selected_path, meta_path]

    outline = load_artifact(workspace, "outline")
    slide_count = outline["meta"]["total_slides"]
    chunks = load_template_chunks()

    # Build enriched search query from outline
    meta = outline.get("meta", {})
    slides = outline.get("slides", [])

    query_parts = [
        meta.get("project_name", ""),
        meta.get("domain", ""),
        meta.get("audience", ""),
        meta.get("tone", ""),
        meta.get("value_proposition", ""),
    ]
    # Add first 3 slide titles (most informative)
    for s in slides[:3]:
        title = s.get("title", "")
        if title:
            query_parts.append(title)
    # Add key product/technology terms
    product_caps = meta.get("product_capabilities", [])
    if isinstance(product_caps, list):
        query_parts.extend(product_caps[:5])
    elif isinstance(product_caps, str):
        query_parts.append(product_caps[:200])

    query_text = " ".join(str(p) for p in query_parts if p)

    # Also use the outline's domain field as the primary intent signal
    primary_domain = meta.get("domain", "general")
    primary_audience = meta.get("audience", "")

    # ── Retrieval: 3-layer chunks → aggregate by template_id ──
    ranked_chunks = query(
        [
            {
                "id": c["chunk_id"],
                "template_id": c["template_id"],
                "text": c["text"],
                "chunk_type": c["chunk_type"],
                "weight": c["weight"],
                **c,
            }
            for c in chunks
        ],
        query_text,
        top_k=30,  # Fetch many chunks (10 templates × 3 chunks ≈ 30)
    )

    # ── Aggregate: weighted sum by template_id ──
    template_scores: dict[str, float] = {}
    template_chunk_hits: dict[str, list[dict]] = {}

    for chunk in ranked_chunks:
        tid = chunk.get("template_id", "")
        if not tid:
            continue
        weight = chunk.get("weight", 0.33)
        score = chunk.get("score", 0.0)
        template_scores[tid] = template_scores.get(tid, 0.0) + score * weight
        if tid not in template_chunk_hits:
            template_chunk_hits[tid] = []
        template_chunk_hits[tid].append(chunk)

    # Sort by aggregated score
    ranked_templates = sorted(template_scores.items(), key=lambda x: -x[1])

    # ── LLM semantic re-ranking (when available) ──
    if llm_client is not None and ranked_templates:
        try:
            ranked_templates = _llm_rerank_templates(
                llm_client, outline, chunks, ranked_templates,
            )
            logger.info("LLM re-ranked templates: %s", [t[0] for t in ranked_templates[:3]])
        except Exception as exc:
            logger.warning("LLM template re-ranking failed, using retrieval scores: %s", exc)

    # Build ranking info
    ranking = []
    for rank, (tid, agg_score) in enumerate(ranked_templates[:5], start=1):
        hits = template_chunk_hits.get(tid, [])
        chunk_types_hit = [h.get("chunk_type", "?") for h in hits]
        ranking.append({
            "template_id": tid,
            "score": round(agg_score, 4),
            "rank": rank,
            "chunks_hit": len(hits),
            "chunk_types": chunk_types_hit,
            "domain_fit": 0.8 if rank == 1 else 0.5,
            "layout_fit": 0.7,
            "tone_fit": 0.7,
            "notes": f"3-layer chunk retrieval: {len(hits)} chunks matched",
        })

    # Determine best match
    from ppt_agent.retrieval.template_index import load_template_index as _load_idx
    _all_entries = _load_idx()

    best_match, selected_score, usable_ranked = _choose_required_real_template(
        ranked_templates, _all_entries
    )
    confident_match = _has_confident_template_match(usable_ranked)
    margin = (
        usable_ranked[0][1] - usable_ranked[1][1]
        if len(usable_ranked) > 1 else usable_ranked[0][1]
    )
    warnings = []
    if not confident_match:
        warnings.append(
            "A real template was required, so the best available template was "
            "selected despite low retrieval confidence. Page-level selection and "
            "manual review are required."
        )
    selected = {
        "template_id": best_match["template_id"],
        "selection_status": "matched" if confident_match else "matched_low_confidence",
        "score": round(selected_score, 4),
        "reason": (
            f"Selected required real template '{best_match['template_id']}' via "
            "3-layer retrieval; confidence controls risk handling, not template usage."
        ),
        "template_path": best_match.get("path", ""),
        "color_scheme": best_match.get("color_scheme", ""),
        "ranking": ranking,
        "warnings": warnings,
        "template_required": True,
        "confidence_gate": {
            "passed": confident_match,
            "minimum_score": MIN_TEMPLATE_MATCH_SCORE,
            "minimum_margin": MIN_TEMPLATE_MATCH_MARGIN,
            "actual_score": round(selected_score, 4),
            "actual_margin": round(margin, 4),
        },
    }

    # A required template must have real ingested metadata. Substituting
    # fallback zones here would pretend to use a template while bypassing it.
    ingest_meta = load_template_meta(best_match)
    if (
        ingest_meta.get("meta_schema_version") != "2.0"
        or ingest_meta.get("parser_strategy") != "pptx_xml_recursive"
    ):
        template_dir = resolve_template_dir(best_match)
        if (template_dir / "template.pptx").exists():
            from ppt_agent.templates.ingest import refresh_template_meta
            logger.info("Refreshing legacy selected-template metadata: %s", template_dir)
            ingest_meta = refresh_template_meta(template_dir, run_ocr=False)
    meta = build_template_meta_from_ingest(ingest_meta)
    template_zones = _build_template_zones(best_match, meta)

    outputs = [
        write_artifact(workspace, "selected_template", selected),
        write_artifact(workspace, "template_meta", meta),
        write_artifact(workspace, "template_zones", template_zones),
    ]
    return outputs


def _llm_rerank_templates(
    llm_client,
    outline: dict,
    chunks: list[dict],
    retrieval_ranked: list[tuple[str, float]],
) -> list[tuple[str, float]]:
    """Use LLM to semantically re-rank templates based on outline fit.

    Args:
        llm_client: LLM client for semantic ranking.
        outline: The full outline artifact.
        chunks: All template chunks.
        retrieval_ranked: Templates ranked by BM25+vector [(template_id, score), ...].

    Returns:
        Re-ranked list of (template_id, score) tuples with LLM-based scores.
    """
    # Build template summaries from chunks
    tpl_summaries = {}
    for c in chunks:
        tid = c["template_id"]
        if tid not in tpl_summaries:
            tpl_summaries[tid] = {}
        tpl_summaries[tid][c["chunk_type"]] = c["text"]

    # Build prompt
    outline_summary = json.dumps({
        "project": outline.get("meta", {}).get("project_name", ""),
        "domain": outline.get("meta", {}).get("domain", ""),
        "audience": outline.get("meta", {}).get("audience", ""),
        "tone": outline.get("meta", {}).get("tone", ""),
        "slide_count": outline.get("meta", {}).get("total_slides", 0),
        "slides": [
            {"title": s.get("title", ""),
             "bullets_count": len(s.get("bullets", []))}
            for s in outline.get("slides", [])[:5]
        ],
    }, ensure_ascii=False)

    # Show top 5 candidates to LLM
    candidates = retrieval_ranked[:5]
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
        f"## 项目大纲\n{outline_summary}\n\n"
        f"## 候选模板（已按关键词相关度预排序）\n"
        f"{json.dumps(tpl_list, ensure_ascii=False, indent=2)}\n\n"
        "## 任务\n"
        "根据项目大纲的内容（领域、受众、风格、页数），从候选模板中选择最合适的模板。\n"
        "考虑因素：\n"
        "1. 领域匹配：模板的行业/场景是否匹配项目\n"
        "2. 风格匹配：视觉风格是否符合项目调性\n"
        "3. 页数适配：模板页数是否足够覆盖项目需求\n"
        "4. 布局适配：模板的页面类型（封面/内容/图表）是否满足需求\n\n"
        "返回JSON: {\"rankings\": [{\"template_id\": \"...\", \"score\": 0.0-1.0, \"reason\": \"...\"}]}\n"
        "按score降序排列所有候选模板。"
    )

    result = llm_client.generate_json(
        prompt=prompt,
        context={},
        system="You are a presentation design expert. Select the best PPT template.",
        phase="template_matching",
        fallback={"rankings": [{"template_id": tid, "score": 1.0 - i * 0.1, "reason": "fallback"}
                              for i, (tid, _) in enumerate(candidates)]},
    )

    # Parse LLM rankings
    rankings = result.get("rankings", [])
    llm_scored = []
    for r in rankings:
        tid = r.get("template_id", "")
        score = float(r.get("score", 0.5))
        reason = r.get("reason", "")
        logger.debug("LLM template rank: %s score=%.3f reason=%s", tid, score, reason[:80])
        llm_scored.append((tid, score))

    # Keep templates not ranked by LLM (beyond top 5)
    for tid, score in retrieval_ranked[5:]:
        llm_scored.append((tid, score * 0.5))  # Down-weight unranked

    # Sort by LLM score
    llm_scored.sort(key=lambda x: -x[1])
    return llm_scored


def _build_template_zones(entry: dict, meta: dict) -> dict:
    """Build the template_zones artifact for a real template.

    Contains per-slide: image path (for img2img reference), text zones
    (for content overlay), and image zones (for visual areas).
    """
    slide_images = get_slide_image_paths(entry)
    slide_zones = get_slide_zones(entry)

    slides: list[dict] = []
    for slide in meta.get("slides", []):
        idx = slide["index"]
        img_path = slide_images.get(idx)

        # Separate text zones from decoration/image zones
        text_zones = [z for z in slide.get("zones", []) if z.get("type") in ("title", "subtitle", "body", "footer", "bullets", "chart")]
        image_zones = [z for z in slide.get("zones", []) if z.get("type") == "image"]

        slides.append({
            "index": idx,
            "template_image": str(img_path) if img_path else None,
            "text_zones": text_zones,
            "image_zones": image_zones,
            "all_zones": slide.get("zones", []),
            "layout": slide.get("layout", "fallback.basic"),
        })

    return {
        "template_id": meta.get("template_id", "unknown"),
        "color_scheme": meta.get("color_scheme", ""),
        "theme_colors": meta.get("theme_colors", {}),
        "slide_count": len(slides),
        "slides": slides,
    }


def _build_fallback_zones(slide_count: int) -> dict:
    """Build template_zones for the fallback template (no real images)."""
    from ppt_agent.models.template_meta import DEFAULT_ZONES

    slides = []
    for i in range(slide_count):
        text_zones = [z for z in DEFAULT_ZONES if z["type"] in ("title", "body", "bullets", "chart")]
        image_zones = [z for z in DEFAULT_ZONES if z["type"] == "image"]
        slides.append({
            "index": i,
            "template_image": None,
            "text_zones": text_zones,
            "image_zones": image_zones,
            "all_zones": DEFAULT_ZONES,
            "layout": "cover.hero" if i == 0 else "fallback.basic",
        })

    return {
        "template_id": "fallback.default",
        "color_scheme": "blue",
        "theme_colors": {
            "primary": "#1F4E79",
            "secondary": "#70AD47",
            "accent": "#F4B183",
            "background": "#FFFFFF",
        },
        "slide_count": slide_count,
        "slides": slides,
    }
