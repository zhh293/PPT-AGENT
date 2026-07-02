from __future__ import annotations

from ppt_agent.models.image_generation import fallback_image_report


def generate_or_fallback(job_id: str, slide_count: int) -> dict:
    return fallback_image_report(job_id, slide_count)
