from __future__ import annotations

from copy import deepcopy
import logging
from pathlib import Path

from ppt_agent.assembly.ppt_writer import write_pptx_from_mapping
from ppt_agent.coordinator.phase_state import load_artifact
from ppt_agent.models.artifacts import JobWorkspace, atomic_write_json

logger = logging.getLogger(__name__)


def _strict_content_mapping_issues(
    slide_contents: dict,
    expected_slide_indices: list[int] | None = None,
) -> list[str]:
    """Return hard text-quality issues that ``force`` may not bypass."""
    issues: list[str] = []
    text_types = {"title", "subtitle", "bullets", "body", "footer", "decorative"}
    actual_indices = [
        int(slide.get("slide_index", -1)) for slide in slide_contents.get("slides", [])
    ]
    if len(set(actual_indices)) != len(actual_indices):
        issues.append("duplicate_slide_index")
    if expected_slide_indices is not None and actual_indices != expected_slide_indices:
        issues.append(
            f"slide_index_mismatch:expected={expected_slide_indices}:actual={actual_indices}"
        )
    for slide in slide_contents.get("slides", []):
        slide_index = int(slide.get("slide_index", 0))
        for zone in slide.get("zones", []):
            if zone.get("type") not in text_types or not zone.get("editable", True):
                continue
            zone_id = zone.get("zone_id", "")
            action = zone.get("action", "")
            content = zone.get("content")
            if action == "clear_text":
                issues.append(f"slide_{slide_index}:clear_text_forbidden:{zone_id}")
            if action == "replace_text" and (
                content is None or content == "" or content == []
            ):
                issues.append(f"slide_{slide_index}:empty_text:{zone_id}")
            if zone.get("fit_status") == "overflow":
                issues.append(f"slide_{slide_index}:overflow:{zone_id}")
            rendered = str(content or "").strip().lower()
            if action == "replace_text" and rendered in {"u", "v", "w", "•", "●"}:
                issues.append(f"slide_{slide_index}:orphan_bullet:{zone_id}")
        for flag in slide.get("fallback_flags", []):
            if str(flag).startswith((
                "length_mismatch:", "clear_text_forbidden:",
                "missing_replacement_text:", "content_eligibility_violation:",
                "llm_constraint_unresolved:", "missing_zone_unresolved:",
                "missing_llm_slide:", "template_sample_preserved:",
                "orphan_bullet_or_glyph:",
            )):
                issues.append(f"slide_{slide_index}:{flag}")
    generation = slide_contents.get("generation_stats")
    if (
        slide_contents.get("mapping_mode") == "llm_first"
        and isinstance(generation, dict)
        and not generation.get("passed", False)
    ):
        issues.append(
            "llm_text_ratio_below_minimum:"
            f"{generation.get('llm_text_ratio', 0)}<"
            f"{generation.get('minimum_llm_text_ratio', 0.85)}"
        )
    return sorted(set(issues))


def _fatal_content_mapping_issues(issues: list[str]) -> list[str]:
    """Return only mapping defects that make deterministic assembly impossible."""
    return [
        issue for issue in issues
        if issue == "duplicate_slide_index"
        or issue.startswith("slide_index_mismatch:")
    ]


def _prepare_best_effort_mapping(
    slide_contents: dict,
    issues: list[str],
) -> tuple[dict, list[dict]]:
    """Make irreducible text defects renderable without hiding them.

    Fit and provenance warnings do not prevent python-pptx from producing a
    usable deck. Empty, cleared, or orphan-glyph replacements are safer when
    reverted to the inherited template shape than when rendered as broken
    content. The original approved artifact remains unchanged; every recovery
    is recorded in ``assembly_mapping_report.json``.
    """
    prepared = deepcopy(slide_contents)
    recoveries: list[dict] = []
    for slide in prepared.get("slides", []):
        slide_index = int(slide.get("slide_index", 0))
        for zone in slide.get("zones", []):
            action = zone.get("action", "")
            content = zone.get("content")
            rendered = str(content or "").strip().lower()
            reason = ""
            if action == "clear_text":
                reason = "clear_text_reverted_to_template"
            elif action == "replace_text" and content in (None, "", []):
                reason = "empty_replacement_reverted_to_template"
            elif action == "replace_text" and rendered in {"u", "v", "w", "•", "●"}:
                reason = "orphan_glyph_reverted_to_template"
            if not reason:
                continue
            zone["action"] = "preserve"
            zone["content"] = None
            recoveries.append({
                "slide_index": slide_index,
                "zone_id": zone.get("zone_id", ""),
                "action": reason,
            })

    prepared["assembly_mapping_warnings"] = list(issues)
    return prepared, recoveries


def _write_mapping_report(
    workspace: JobWorkspace,
    issues: list[str],
    fatal_issues: list[str],
    recoveries: list[dict],
) -> None:
    atomic_write_json(
        workspace.root / "assembly_mapping_report.json",
        {
            "status": "failed" if fatal_issues else (
                "passed_with_warnings" if issues else "passed"
            ),
            "issue_count": len(issues),
            "fatal_issue_count": len(fatal_issues),
            "issues": issues,
            "recoveries": recoveries,
            "policy": (
                "Only structural mapping defects block PPT generation; "
                "irreducible text-fit defects are rendered with warnings."
            ),
        },
    )


def _apply_recoveries_to_mappings(
    mappings: dict,
    recoveries: list[dict],
) -> None:
    recovery_ids = {
        (int(item["slide_index"]), item["zone_id"])
        for item in recoveries
    }
    for slide_key, slide_mappings in mappings.items():
        slide_index = int(slide_key)
        for entry in slide_mappings.values():
            if (
                isinstance(entry, dict)
                and (slide_index, entry.get("zone_id", "")) in recovery_ids
            ):
                entry["action"] = "preserve"
                entry["content"] = None


def run(workspace: JobWorkspace, force: bool = False) -> Path:
    output = workspace.root / "final.pptx"
    if output.exists() and not force:
        dependencies = [
            workspace.artifact_path("slide_contents"),
            workspace.artifact_path("image_generation_report"),
        ]
        if all(
            not dependency.exists()
            or output.stat().st_mtime >= dependency.stat().st_mtime
            for dependency in dependencies
        ):
            return output
    slide_contents = load_artifact(workspace, "slide_contents")
    selected_template = load_artifact(workspace, "selected_template")
    if selected_template.get("template_id") == "fallback.default":
        raise ValueError(
            "Real-template assembly is required; fallback.default is not allowed. "
            "Re-run template_matching with at least one ingested real template."
        )
    outline = load_artifact(workspace, "outline")
    expected_slide_indices = [
        int(slide.get("slide_index", index))
        for index, slide in enumerate(outline.get("slides", []))
    ]
    mapping_issues = _strict_content_mapping_issues(
        slide_contents, expected_slide_indices
    )
    fatal_mapping_issues = _fatal_content_mapping_issues(mapping_issues)
    prepared_slide_contents, recoveries = _prepare_best_effort_mapping(
        slide_contents, mapping_issues
    )
    _write_mapping_report(
        workspace,
        mapping_issues,
        fatal_mapping_issues,
        recoveries,
    )
    if fatal_mapping_issues:
        raise ValueError(
            "Cannot assemble PPT: structural mapping gate failed. "
            f"Issues: {fatal_mapping_issues[:20]}"
        )
    if mapping_issues:
        logger.warning(
            "Assembling with %d recoverable mapping warning(s): %s",
            len(mapping_issues),
            mapping_issues[:20],
        )
    slide_contents = prepared_slide_contents

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
            outline=outline,
            output_path=output,
            workspace_root=workspace.root,
        )
    else:
        raise FileNotFoundError(
            "Real-template assembly is required, but selected_template does not "
            "resolve to an existing template.pptx."
        )

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
    outline = load_artifact(workspace, "outline")
    expected_slide_indices = [
        int(slide.get("slide_index", index))
        for index, slide in enumerate(outline.get("slides", []))
    ]
    mapping_issues = _strict_content_mapping_issues(
        slide_contents, expected_slide_indices
    )
    fatal_mapping_issues = _fatal_content_mapping_issues(mapping_issues)
    prepared_slide_contents, recoveries = _prepare_best_effort_mapping(
        slide_contents, mapping_issues
    )
    _write_mapping_report(
        workspace,
        mapping_issues,
        fatal_mapping_issues,
        recoveries,
    )
    if fatal_mapping_issues:
        raise ValueError(
            "Cannot assemble PPT: structural mapping gate failed. "
            f"Issues: {fatal_mapping_issues[:20]}"
        )
    if mapping_issues:
        logger.warning(
            "Agent-driven assembly continuing with %d recoverable mapping warning(s).",
            len(mapping_issues),
        )
    slide_contents = prepared_slide_contents
    _apply_recoveries_to_mappings(mappings, recoveries)

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
        outline=outline,
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
                if zid:
                    mappings[idx][zid] = {
                        "zone_id": zid,
                        "type": ztype,
                        "action": zone.get(
                            "action", "replace_text" if content is not None else "preserve"
                        ),
                        "content": content,
                    }
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
        template_index = int(slide.get("template_slide_index", idx))
        tpl_slide = tpl_slides.get(template_index, {})
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

            chars_per_line = max(5, int(w * 650 / max(font_pt, 10)))
            max_lines = max(1, int(h * 900 / max(font_pt, 10)))

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
