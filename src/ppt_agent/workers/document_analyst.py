from __future__ import annotations

import re
from pathlib import Path

from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.models.source_summary import ImageInventoryItem, SourceSummary
from ppt_agent.coordinator.phase_state import write_artifact
from ppt_agent.tools.documents import extract_text

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


def _infer_project_name(text: str, files: list[Path]) -> str:
    for line in text.splitlines():
        clean = line.strip(" #\t")
        if 3 <= len(clean) <= 60:
            return clean
    return files[0].stem if files else "Untitled Project"


def _sentences(text: str) -> list[str]:
    chunks = re.split(r"[。！？.!?\n]+", text)
    return [chunk.strip() for chunk in chunks if len(chunk.strip()) > 8]


def run(workspace: JobWorkspace, force: bool = False) -> Path:
    output = workspace.artifact_path("source_summary")
    if output.exists() and not force:
        return output
    files = [p for p in workspace.input_dir.rglob("*") if p.is_file()]
    text_parts = []
    warnings = []
    images = []
    for idx, path in enumerate(files):
        if path.suffix.lower() in IMAGE_SUFFIXES:
            usage = "screenshot" if any(token in path.name.lower() for token in ["screen", "截图", "界面"]) else "reference_image"
            images.append(ImageInventoryItem(f"img_{idx+1}", str(path.relative_to(workspace.root)), usage, f"User supplied image {path.name}"))
            continue
        extracted = extract_text(path)
        if extracted:
            text_parts.append(f"[{path.name}]\n{extracted}")
        else:
            warnings.append(f"No text extracted from {path.name}")
    full_text = "\n\n".join(text_parts)
    sentences = _sentences(full_text)
    project_name = _infer_project_name(full_text, files)
    capabilities = sentences[:5] or ["Project materials were received and summarized for a fallback presentation."]
    evidence_payload = [
        {"evidence_id": f"ev_{i+1}", "summary": sentence[:180], "source_refs": [files[0].name if files else "input"], "confidence": 0.7}
        for i, sentence in enumerate(sentences[:5])
    ] or [{"evidence_id": "ev_1", "summary": capabilities[0], "source_refs": [files[0].name if files else "input"], "confidence": 0.55}]
    summary = SourceSummary(
        project_name=project_name,
        domain="software/product" if re.search(r"系统|平台|软件|产品|API|数据", full_text, re.I) else "general",
        target_audience="business stakeholders",
        tone="professional",
        value_proposition=sentences[0] if sentences else "Create a clear project presentation from the provided materials.",
        product_capabilities=capabilities,
        evidence_items=[],
        image_inventory=images,
        warnings=warnings,
        confidence=0.72 if full_text else 0.45,
    ).to_dict()
    summary["evidence_items"] = evidence_payload
    return write_artifact(workspace, "source_summary", summary)
