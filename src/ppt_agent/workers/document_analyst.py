from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Union

from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.models.source_summary import ImageInventoryItem, SourceSummary
from ppt_agent.coordinator.phase_state import write_artifact
from ppt_agent.tools.documents import extract_text

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "document_analysis.md"


def _load_prompt() -> str:
    """Load the document analysis prompt template."""
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text(encoding="utf-8")
    return "Analyze the following project materials and produce a structured JSON summary."


def _infer_project_name(text: str, files: list[Path]) -> str:
    for line in text.splitlines():
        clean = line.strip(" #\t")
        if 3 <= len(clean) <= 60:
            return clean
    return files[0].stem if files else "Untitled Project"


def _sentences(text: str) -> list[str]:
    chunks = re.split(r"[。！？.!?\n]+", text)
    return [chunk.strip() for chunk in chunks if len(chunk.strip()) > 8]


def _fallback_analysis(full_text: str, files: list[Path], images: list[ImageInventoryItem], warnings: list[str]) -> dict:
    """Original heuristic analysis — used when no LLM is available."""
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
    return summary


def _llm_analysis(llm_client, full_text: str, files: list[Path], images: list[ImageInventoryItem], warnings: list[str]) -> dict:
    """Use LLM for intelligent document analysis."""
    prompt = _load_prompt()

    # Build context with extracted text and file info
    file_info = [{"name": f.name, "type": f.suffix} for f in files]
    image_info = [img.__dict__ if hasattr(img, '__dict__') else {"image_id": img.image_id, "path": img.path, "detected_usage": img.detected_usage, "summary": img.summary} for img in images]

    context = {
        "extracted_text": full_text[:15000],  # Limit to avoid context overflow
        "files": file_info,
        "images": image_info,
        "existing_warnings": warnings,
    }

    fallback = _fallback_analysis(full_text, files, images, warnings)

    from ppt_agent.llm.schemas import SOURCE_SUMMARY_SCHEMA

    result = llm_client.generate_json(
        prompt=prompt,
        context=context,
        system="You are a document analyst for PPT generation. Output only valid JSON.",
        phase="document_analysis",
        fallback=fallback,
        schema=SOURCE_SUMMARY_SCHEMA,
    )

    # Ensure required fields exist and image_inventory is preserved
    result.setdefault("project_name", _infer_project_name(full_text, files))
    result.setdefault("domain", "general")
    result.setdefault("target_audience", "business stakeholders")
    result.setdefault("tone", "professional")
    result.setdefault("value_proposition", "")
    result.setdefault("product_capabilities", [])
    result.setdefault("evidence_items", [])
    result.setdefault("unsupported_claims", [])
    result.setdefault("warnings", warnings)
    result.setdefault("confidence", 0.8)

    # Always use the actual image inventory from file scanning
    result["image_inventory"] = [
        {"image_id": img.image_id, "path": img.path, "detected_usage": img.detected_usage, "summary": img.summary, "relevance": img.relevance}
        for img in images
    ]

    # Validate evidence items have required fields
    for i, ev in enumerate(result.get("evidence_items", [])):
        ev.setdefault("evidence_id", f"ev_{i+1}")
        ev.setdefault("source_refs", [files[0].name if files else "input"])
        ev.setdefault("confidence", 0.7)

    return result


def run(workspace: JobWorkspace, force: bool = False, llm_client=None) -> Path:
    output = workspace.artifact_path("source_summary")
    if output.exists() and not force:
        return output

    files = [p for p in workspace.input_dir.rglob("*") if p.is_file()]
    text_parts: list[tuple[int, str]] = []  # (index, text) for ordering
    warnings: list[str] = []
    images: list[ImageInventoryItem] = []

    def _process_file(idx: int, path: Path) -> Union[ImageInventoryItem, tuple[int, str], str]:
        """Process a single file — returns an image item, (idx, text), or a warning string."""
        if path.suffix.lower() in IMAGE_SUFFIXES:
            usage = "screenshot" if any(token in path.name.lower() for token in ["screen", "截图", "界面"]) else "reference_image"
            return ImageInventoryItem(f"img_{idx+1}", str(path.relative_to(workspace.root)), usage, f"User supplied image {path.name}")
        extracted = extract_text(path)
        if extracted:
            return (idx, f"[{path.name}]\n{extracted}")
        return f"No text extracted from {path.name}"

    with ThreadPoolExecutor(max_workers=min(8, len(files) or 1)) as pool:
        futures = {pool.submit(_process_file, idx, path): idx for idx, path in enumerate(files)}
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:
                warnings.append(f"Error processing file index {futures[future]}: {exc}")
                continue
            if isinstance(result, ImageInventoryItem):
                images.append(result)
            elif isinstance(result, tuple):
                text_parts.append(result)
            else:
                # It is a warning string
                warnings.append(result)

    # Sort text parts by original file index to preserve deterministic order
    text_parts.sort(key=lambda t: t[0])
    full_text = "\n\n".join(text for _, text in text_parts)

    if llm_client is not None:
        logger.info("Using LLM for document analysis")
        summary = _llm_analysis(llm_client, full_text, files, images, warnings)
    else:
        logger.info("No LLM client, using fallback document analysis")
        summary = _fallback_analysis(full_text, files, images, warnings)

    return write_artifact(workspace, "source_summary", summary)
