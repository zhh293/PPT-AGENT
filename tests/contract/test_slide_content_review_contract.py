from __future__ import annotations

from ppt_agent.models.slide_contents import approve_slide_contents


def test_slide_content_review_approval_marks_all_text_editable() -> None:
    payload = {
        "template_id": "fallback.default",
        "review_status": "draft",
        "slides": [
            {
                "slide_index": 0,
                "layout": "cover.hero",
                "review_status": "edited",
                "zones": [{"zone_id": "title", "type": "title", "position": [0, 0, 1, 0.2], "editable": True, "content": "Edited"}],
            }
        ],
    }
    approved = approve_slide_contents(payload)
    assert approved["review_status"] == "approved"
    assert approved["slides"][0]["review_status"] == "approved"
