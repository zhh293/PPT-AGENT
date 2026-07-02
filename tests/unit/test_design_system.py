from __future__ import annotations

from ppt_agent.design.design_scorer import score_slide
from ppt_agent.design.visual_density import classify_density


def test_design_primitives() -> None:
    assert classify_density("Title", ["short"]) == "low"
    score, suggestions = score_slide({"zones": [{"type": "bullets", "content": ["x"] * 7}]})
    assert score < 80
    assert suggestions
