from __future__ import annotations


def fallback_image_report(job_id: str, slide_count: int | list[int]) -> dict:
    slide_indices = (
        list(range(slide_count))
        if isinstance(slide_count, int)
        else sorted({int(index) for index in slide_count})
    )
    return {
        "job_id": job_id,
        "status": "succeeded_with_fallbacks",
        "total_slides": len(slide_indices),
        "generated": 0,
        "fallback": len(slide_indices),
        "summary": "Visual generation skipped; fallback placeholders used.",
        "provider": "fallback",
        "slide_results": [
            {"slide_index": i, "mode": "region", "status": "fallback_used", "fallback": "placeholder", "manual_review": True}
            for i in slide_indices
        ],
        "warnings": ["External visual generation was not configured."],
    }
