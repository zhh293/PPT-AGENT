from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class OutlineSlide:
    slide_index: int
    type: str
    title: str
    purpose: str
    image_needs: str
    bullets: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    priority: str = "recommended"


@dataclass
class PresentationOutline:
    meta: dict
    slides: list[OutlineSlide]

    def to_dict(self) -> dict:
        return {"meta": self.meta, "slides": [asdict(slide) for slide in self.slides]}
