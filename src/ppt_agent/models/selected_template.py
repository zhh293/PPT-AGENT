from __future__ import annotations


def fallback_selection(reason: str = "No configured template library match was available.") -> dict:
    return {
        "template_id": "fallback.default",
        "selection_status": "fallback",
        "score": 0.5,
        "reason": "Using built-in fallback template.",
        "fallback_reason": reason,
        "ranking": [
            {
                "template_id": "fallback.default",
                "score": 0.5,
                "rank": 1,
                "domain_fit": 0.5,
                "layout_fit": 0.7,
                "tone_fit": 0.6,
                "notes": "Deterministic fallback template.",
            }
        ],
        "warnings": [reason],
    }
