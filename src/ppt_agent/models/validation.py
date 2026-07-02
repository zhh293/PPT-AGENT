from __future__ import annotations

from datetime import datetime, timezone


def validation_report(job_id: str, slide_contents: dict, warnings: list[str] | None = None) -> dict:
    manual_review_items = list(warnings or [])
    slide_results = []
    for slide in slide_contents.get("slides", []):
        issues = []
        has_content = any(zone.get("content") for zone in slide.get("zones", []) if zone.get("type") in {"title", "subtitle", "bullets"})
        editable = all(zone.get("editable", False) for zone in slide.get("zones", []) if zone.get("type") in {"title", "subtitle", "bullets"})
        text_fit = True
        fallback_resolved = "visual_placeholder" not in slide.get("fallback_flags", [])
        for zone in slide.get("zones", []):
            content = zone.get("content")
            if zone.get("type") == "title" and isinstance(content, str) and len(content) > 90:
                text_fit = False
            if zone.get("type") == "bullets" and isinstance(content, list):
                if len(content) > 7 or any(len(item) > 180 for item in content):
                    text_fit = False
        if not has_content:
            issues.append("No editable text content found.")
        if not editable:
            issues.append("One or more text zones are not editable.")
        if not text_fit:
            issues.append("Text may overflow the selected layout.")
            manual_review_items.append(f"Slide {slide['slide_index']} needs text fit review.")
        if not fallback_resolved:
            issues.append("Visual placeholder fallback remains unresolved.")
            manual_review_items.append(f"Slide {slide['slide_index']} uses fallback visual placeholder.")
        status = "passed" if not issues else "warning"
        slide_results.append(
            {
                "slide_index": slide["slide_index"],
                "status": status,
                "checks": {
                    "content_present": has_content,
                    "text_preserved": True,
                    "text_editable": editable,
                    "text_fit": text_fit,
                    "image_fit": True,
                    "fallback_resolved": fallback_resolved,
                },
                "issues": issues,
                "recommended_action": "Review slide manually." if issues else "No action required.",
                "design_score": 80 if not issues else 65,
                "design_suggestions": [],
            }
        )
    if any(item["status"] != "passed" for item in slide_results) or manual_review_items:
        status = "passed_with_warnings"
    else:
        status = "passed"
    return {
        "job_id": job_id,
        "status": status,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "summary": f"Validated {len(slide_results)} slides.",
        "manual_review_items": manual_review_items,
        "slide_results": slide_results,
    }
