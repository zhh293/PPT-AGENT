from __future__ import annotations

import logging
from pathlib import Path

from ppt_agent.assembly.ppt_writer import write_pptx, write_pptx_from_mapping
from ppt_agent.coordinator.phase_state import load_artifact
from ppt_agent.models.artifacts import JobWorkspace

logger = logging.getLogger(__name__)


def run(workspace: JobWorkspace, force: bool = False) -> Path:
    output = workspace.root / "final.pptx"
    if output.exists() and not force:
        return output
    slide_contents = load_artifact(workspace, "slide_contents")

    review_status = slide_contents.get("review_status")
    logger.info("slide_contents review_status: %s", review_status or "<not set>")

    if review_status != "approved" and not force:
        raise ValueError(
            "Cannot assemble PPT: slide_contents.json has not been approved by the user "
            f"(current review_status={review_status!r}). "
            "Please review slide_contents.json, set \"review_status\" to \"approved\", "
            "then re-run. Alternatively, pass force=True to assemble a draft without approval."
        )

    template_path = _find_template_pptx(workspace)

    if template_path:
        logger.info("Using template: %s — zone_id precise assembly", template_path)
        template_zones = load_artifact(workspace, "template_zones")
        mappings, image_mappings = _build_mappings_from_slide_contents(slide_contents)
        logger.info(
            "Built mappings for %d slides, %d with image mappings",
            len(mappings),
            sum(1 for im in image_mappings.values() if im),
        )
        write_pptx_from_mapping(
            template_path=template_path,
            template_zones=template_zones,
            mappings=mappings,
            image_mappings=image_mappings,
            slide_contents=slide_contents,
            output_path=output,
            workspace_root=workspace.root,
        )
    else:
        logger.info("No template PPTX found, falling back to blank assembly")
        write_pptx(slide_contents, output, workspace_root=workspace.root)

    return output


def assemble_with_mapping(
    workspace: JobWorkspace,
    mappings: dict,
    image_mappings: dict | None = None,
    force: bool = False,
) -> Path:
    """Agent-driven assembly — Agent already decided zone_id → content mapping.

    This is the NEW path: the Agent reads ``template_zones.json`` and
    ``slide_contents.json``, reasons about which zone_id gets which content,
    and passes explicit ``mappings`` + ``image_mappings`` dicts.

    Args:
        workspace: Job workspace with all artifacts.
        mappings: ``{slide_index: {content_type: {zone_id, content}}}``
        image_mappings: ``{slide_index: {zone_id: image_path}}``
        force: Skip review_status check.

    Returns:
        Path to the generated ``final.pptx``.

    Raises:
        FileNotFoundError: If no template PPTX is found.
    """
    if image_mappings is None:
        image_mappings = {}

    template_path = _find_template_pptx(workspace)
    if not template_path:
        raise FileNotFoundError(
            "No template PPTX found — cannot run agent-driven assembly. "
            "Ensure selected_template.json has a valid template_path."
        )

    slide_contents = load_artifact(workspace, "slide_contents")

    review_status = slide_contents.get("review_status")
    if review_status != "approved" and not force:
        raise ValueError(
            "Cannot assemble PPT: slide_contents.json has not been approved. "
            f"(review_status={review_status!r}). "
            "Approve it or pass force=True."
        )

    template_zones = load_artifact(workspace, "template_zones")
    output = workspace.root / "final.pptx"

    from ppt_agent.assembly.ppt_writer import write_pptx_from_mapping
    write_pptx_from_mapping(
        template_path=template_path,
        template_zones=template_zones,
        mappings=mappings,
        image_mappings=image_mappings,
        slide_contents=slide_contents,
        output_path=output,
        workspace_root=workspace.root,
    )
    return output


def _build_mappings_from_slide_contents(slide_contents: dict) -> tuple[dict, dict]:
    """Extract zone_id → content mappings from slide_contents.

    slide_contents already has zone_id per zone — content_mapping did the
    hard work of matching content to template zones.  This function simply
    converts that into the format ``write_pptx_from_mapping`` expects.

    Returns:
        (mappings, image_mappings) — both keyed by slide_index.
    """
    mappings: dict[int, dict] = {}
    image_mappings: dict[int, dict] = {}

    for slide in slide_contents.get("slides", []):
        idx: int = slide["slide_index"]
        mappings[idx] = {}
        image_mappings[idx] = {}

        for zone in slide.get("zones", []):
            zid = zone.get("zone_id", "")
            ztype = zone.get("type", "")

            if ztype in ("title", "subtitle", "bullets", "body", "footer"):
                content = zone.get("content")
                if zid and content is not None:
                    mappings[idx][ztype] = {"zone_id": zid, "content": content}
            elif ztype == "image":
                if zid:
                    # Prefer user-uploaded → generated → explicit image_path
                    img = zone.get("image_ref") or zone.get("generated_image") or zone.get("image_path")
                    if img:
                        image_mappings[idx][zid] = str(img)

    return mappings, image_mappings


def _build_overflow_report(
    slide_contents: dict,
    template_zones: dict,
) -> list[dict]:
    """Identify zones where content may overflow the template shape capacity.

    Uses the same capacity model as ``_estimate_capacity`` in content_mapper.
    Returns a list of zones that need LLM attention — zones that fit are
    intentionally omitted (no noise for the LLM).
    """
    tpl_slides: dict[int, dict] = {
        s["index"]: s for s in template_zones.get("slides", [])
    }
    overflow_zones: list[dict] = []

    for slide in slide_contents.get("slides", []):
        idx: int = slide["slide_index"]
        tpl_slide = tpl_slides.get(idx, {})
        if not tpl_slide:
            continue

        all_zones: dict[str, dict] = {
            z.get("zone_id", ""): z for z in tpl_slide.get("all_zones", [])
        }

        for zone in slide.get("zones", []):
            ztype = zone.get("type", "")
            if ztype not in ("title", "subtitle", "bullets", "body"):
                continue

            content = zone.get("content")
            if not content:
                continue

            zid = zone.get("zone_id", "")
            tpl_zone = all_zones.get(zid, {})
            if not tpl_zone:
                continue

            pos = tpl_zone.get("position", [0, 0, 0, 0])
            if len(pos) < 4:
                continue

            fmt = zone.get("formatting", {}) or {}
            # Also check template zone formatting as fallback
            tpl_fmt = tpl_zone.get("formatting", {}) or {}
            font_pt = (
                fmt.get("font_size_pt")
                or tpl_fmt.get("font_size_pt")
                or 14
            )
            font_pt = max(int(font_pt), 8)
            w, h = pos[2], pos[3]

            chars_per_line = max(5, int(w * 10 * (10 / max(font_pt, 10))))
            max_lines = max(1, int(h * 720 / max(font_pt, 10)))

            overflow = False
            details: dict = {"overflow_type": "", "current_size": "", "capacity": ""}

            if ztype in ("title", "subtitle"):
                text = str(content)
                actual_chars = len(text)
                details["current_size"] = f"{actual_chars} 字"
                details["capacity"] = f"{chars_per_line} 字/行 × {max_lines} 行"
                if actual_chars > chars_per_line:
                    overflow = True
                    details["overflow_type"] = "chars_exceeded"
                if max_lines < 1:
                    overflow = True
                    details["overflow_type"] = "zone_too_small"

            elif ztype in ("bullets", "body"):
                items = content if isinstance(content, list) else [str(content)]
                actual_lines = len(items)
                max_item_len = max((len(str(it)) for it in items), default=0)
                details["current_size"] = f"{actual_lines} 条, 最长 {max_item_len} 字"
                details["capacity"] = f"{chars_per_line} 字/行 × {max_lines} 行"
                if actual_lines > max_lines:
                    overflow = True
                    details["overflow_type"] = "lines_exceeded"
                if max_item_len > chars_per_line:
                    overflow = True
                    details["overflow_type"] = (
                        f"{details['overflow_type']}+chars_exceeded"
                        if details["overflow_type"]
                        else "chars_exceeded"
                    )

            if overflow:
                overflow_zones.append({
                    "slide_index": idx,
                    "zone_id": zid,
                    "type": ztype,
                    "formatting": {
                        "font_name": fmt.get("font_name") or tpl_fmt.get("font_name", ""),
                        "font_size_pt": font_pt,
                    },
                    "capacity": details["capacity"],
                    "current_content": content,
                    "overflow": details["overflow_type"],
                })

    return overflow_zones


def _merge_adaptations(
    base_mappings: dict[int, dict],
    adaptations: dict | None,
) -> dict[int, dict]:
    """Merge LLM content adaptations into base mappings.

    **Only ``content`` is allowed to change.**  zone_id is immutable —
    adaptations that attempt to change zone_id, formatting, or position
    are silently ignored.

    Args:
        base_mappings: From ``_build_mappings_from_slide_contents``.
        adaptations: ``{slide_index: {content_type: {content: ...}}}``
            as provided by the LLM.  May be ``None`` or empty.

    Returns:
        Merged mappings (mutates *base_mappings* in place and returns it).
    """
    if not adaptations:
        return base_mappings

    for key, slide_adapts in adaptations.items():
        slide_idx = int(key) if not isinstance(key, int) else key
        if slide_idx not in base_mappings:
            continue

        if not isinstance(slide_adapts, dict):
            continue

        for content_type, adapt in slide_adapts.items():
            if not isinstance(adapt, dict):
                continue
            if content_type not in base_mappings[slide_idx]:
                continue

            new_content = adapt.get("content")
            if new_content is not None and new_content != "":
                base_mappings[slide_idx][content_type]["content"] = new_content

    return base_mappings


def _find_template_pptx(workspace: JobWorkspace) -> Path | None:
    """Find the template PPTX file from the selected_template artifact."""
    try:
        selected = load_artifact(workspace, "selected_template")
    except (FileNotFoundError, Exception):
        return None

    template_path_str = selected.get("template_path", "")
    if not template_path_str:
        return None

    # template_path is relative to templates/ root
    tpl_pptx = Path("templates") / template_path_str / "template.pptx"
    if tpl_pptx.exists():
        return tpl_pptx

    # Also check if auto_ingest preserved the file
    alternate = Path("templates") / template_path_str / "template.pptx"
    # Try common locations
    for p in [tpl_pptx, alternate]:
        if p.exists():
            return p

    return None
