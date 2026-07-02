from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ARTIFACT_NAMES = {
    "source_summary": "source_summary.json",
    "outline": "outline.json",
    "selected_template": "selected_template.json",
    "template_meta": "template_meta.json",
    "slide_design_plan": "slide_design_plan.json",
    "slide_contents": "slide_contents.json",
    "image_generation_config": "image_generation_config.json",
    "image_generation_report": "image_generation_report.json",
    "validation_report": "validation_report.json",
}


@dataclass(frozen=True)
class JobWorkspace:
    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).resolve())

    @property
    def input_dir(self) -> Path:
        return self.root / "input"

    @property
    def generated_slides_dir(self) -> Path:
        return self.root / "generated_slides"

    @property
    def history_path(self) -> Path:
        return self.root / "history.jsonl"

    def artifact_path(self, name: str) -> Path:
        filename = ARTIFACT_NAMES.get(name, name)
        return self.root / filename

    def ensure(self) -> None:
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.generated_slides_dir.mkdir(parents=True, exist_ok=True)
        (self.root / "layer_analysis").mkdir(parents=True, exist_ok=True)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
