from __future__ import annotations

from dataclasses import asdict, dataclass, field

VALID_TRANSITIONS = {
    "draft": {"edited", "approved", "needs_review"},
    "edited": {"approved", "needs_review"},
    "needs_review": {"edited", "approved"},
    "approved": set(),
}


@dataclass
class SlideZoneContent:
    zone_id: str
    type: str           # "title", "bullets", "image", "footer", etc.
    position: list[float]  # [x, y, w, h] as 0-1 fractions
    editable: bool
    content: str | list[str] | None = None
    source: str = "none"
    image_ref: str | None = None
    image_prompt: str | None = None
    fit_status: str = "unknown"

    # Convenience alias
    @property
    def zone_type(self) -> str:
        return self.type

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class SlideContent:
    slide_index: int
    layout: str
    zones: list[SlideZoneContent]
    layout_id: str = "fallback.basic"
    visual_density: str = "medium"
    review_status: str = "draft"
    source_refs: list[str] = field(default_factory=list)
    fallback_flags: list[str] = field(default_factory=list)
    template_image: str | None = None  # Path to template slide image for img2img

    def to_dict(self) -> dict:
        data = asdict(self)
        data["zones"] = [zone.to_dict() for zone in self.zones]
        return data


def aggregate_review_status(slides: list[dict]) -> str:
    statuses = {slide.get("review_status", "draft") for slide in slides}
    if statuses == {"approved"}:
        return "approved"
    if "needs_review" in statuses:
        return "needs_review"
    if "edited" in statuses:
        return "edited"
    return "draft"


def approve_slide_contents(payload: dict) -> dict:
    for slide in payload.get("slides", []):
        current = slide.get("review_status", "draft")
        if current != "approved" and "approved" not in VALID_TRANSITIONS.get(current, set()):
            raise ValueError(f"Cannot approve slide {slide.get('slide_index')} from {current}")
        slide["review_status"] = "approved"
        for zone in slide.get("zones", []):
            if zone.get("type") in {"title", "subtitle", "bullets"}:
                zone["editable"] = True
    payload["review_status"] = "approved"
    validate_slide_sequence(payload)
    return payload


def validate_slide_sequence(payload: dict) -> None:
    indexes = [slide["slide_index"] for slide in payload.get("slides", [])]
    if len(indexes) != len(set(indexes)):
        raise ValueError("slide indexes must be unique")
    if indexes != sorted(indexes):
        for new_index, slide in enumerate(payload.get("slides", [])):
            slide["slide_index"] = new_index
