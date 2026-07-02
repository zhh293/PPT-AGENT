from __future__ import annotations


def fallback_image_report(job_id: str, slide_count: int) -> dict:
    return {
        "job_id": job_id,
        "status": "succeeded_with_fallbacks",
        "summary": "Visual generation skipped; fallback placeholders used.",
        "provider": "fallback",
        "slide_results": [
            {"slide_index": i, "mode": "region", "status": "fallback_used", "fallback": "placeholder", "manual_review": True}
            for i in range(slide_count)
        ],
        "warnings": ["External visual generation was not configured."],
    }
