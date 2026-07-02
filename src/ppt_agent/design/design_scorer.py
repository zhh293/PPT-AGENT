from __future__ import annotations


def score_slide(slide: dict) -> tuple[int, list[str]]:
    bullets = []
    for zone in slide.get("zones", []):
        if zone.get("type") == "bullets" and isinstance(zone.get("content"), list):
            bullets = zone["content"]
    if len(bullets) > 6:
        return 65, ["Reduce bullet count for readability."]
    return 85, []
