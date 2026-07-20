from __future__ import annotations

import json
import hashlib
import logging
from pathlib import Path

from pptx import Presentation

from ppt_agent.coordinator.phase_state import load_artifact, write_artifact
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.models.validation import validation_report

logger = logging.getLogger(__name__)


def _strict_assembly_invariants(
    final_pptx: Path,
    template_zones: dict,
    slide_contents: dict,
) -> dict:
    """Verify that strict assembly changed text/image payloads only.

    Geometry and formatting are compared with fingerprints captured during
    template ingestion.  Slides in the output are aligned to the selected
    template pages recorded by the mapper.
    """
    from ppt_agent.templates.ingest import _extract_shape_formatting, _iter_shapes_recursive

    result = {"applicable": False, "passed": True, "checked_zones": 0, "issues": []}
    if slide_contents.get("assembly_policy", {}).get("mode") != "text_replace_only":
        return result
    if not final_pptx.exists():
        return result

    template_by_index = {
        int(slide.get("index", index)): slide
        for index, slide in enumerate(template_zones.get("slides", []))
    }
    if not any(
        zone.get("geometry_fingerprint") or zone.get("formatting_fingerprint")
        for slide in template_by_index.values()
        for zone in slide.get("all_zones", [])
    ):
        return result

    result["applicable"] = True
    presentation = Presentation(str(final_pptx))
    for output_index, mapped_slide in enumerate(slide_contents.get("slides", [])):
        if output_index >= len(presentation.slides):
            result["issues"].append({
                "slide_index": output_index,
                "kind": "missing_output_slide",
            })
            continue
        template_index = int(mapped_slide.get("template_slide_index", output_index))
        template_slide = template_by_index.get(template_index, {})
        shape_items = list(_iter_shapes_recursive(
            presentation.slides[output_index].shapes, template_index
        ))
        shapes_by_path = {item["shape_path"]: item for item in shape_items}
        shapes_by_id = {
            int(item["shape"].shape_id): item for item in shape_items
        }
        for zone in template_slide.get("all_zones", []):
            expected_geometry = zone.get("geometry_fingerprint")
            # Font/paragraph formatting only applies to editable text shapes.
            # Non-text zones store an empty-dict fingerprint during ingestion;
            # comparing that to a text-format default dict creates false drift.
            expected_formatting = (
                zone.get("formatting_fingerprint") if zone.get("editable") else None
            )
            if not expected_geometry and not expected_formatting:
                continue
            result["checked_zones"] += 1
            native_shape_id = zone.get("native_shape_id")
            item = shapes_by_path.get(zone.get("shape_path") or zone.get("zone_id"))
            if item is None and native_shape_id is not None:
                item = shapes_by_id.get(int(native_shape_id))
            if item is None:
                result["issues"].append({
                    "slide_index": output_index,
                    "template_slide_index": template_index,
                    "zone_id": zone.get("zone_id", ""),
                    "kind": "missing_shape",
                })
                continue
            shape = item["shape"]

            geometry_payload = ":".join(
                str(round(value, 3)) for value in item["displayed_geometry"]
            )
            actual_geometry = hashlib.sha256(
                geometry_payload.encode("utf-8")
            ).hexdigest()[:16]
            if expected_geometry and actual_geometry != expected_geometry:
                result["issues"].append({
                    "slide_index": output_index,
                    "template_slide_index": template_index,
                    "zone_id": zone.get("zone_id", ""),
                    "kind": "geometry_drift",
                    "expected": expected_geometry,
                    "actual": actual_geometry,
                })

            formatting_payload = json.dumps(
                _extract_shape_formatting(shape), sort_keys=True, ensure_ascii=False
            )
            actual_formatting = hashlib.sha256(
                formatting_payload.encode("utf-8")
            ).hexdigest()[:16]
            if expected_formatting and actual_formatting != expected_formatting:
                result["issues"].append({
                    "slide_index": output_index,
                    "template_slide_index": template_index,
                    "zone_id": zone.get("zone_id", ""),
                    "kind": "formatting_drift",
                    "expected": expected_formatting,
                    "actual": actual_formatting,
                })

    result["passed"] = not result["issues"]
    return result

# ── Visual audit integration ──
try:
    from ppt_agent.vision.pptx_audit import audit_pptx as visual_audit_pptx
except ImportError:
    visual_audit_pptx = None
    logger.info("Vision module not available — visual audit skipped")

_FRESH_EYES_SYSTEM = (
    "You are an independent PPT quality reviewer. You have NOT seen any prior "
    "conversation or phase context — you are reviewing the final output with "
    "fresh eyes.\n\n"
    "Evaluate the presentation on three dimensions:\n"
    "1. **Content completeness**: Does slide_contents.json cover all expected "
    "slides, sections, and key points? Are any slides empty or missing?\n"
    "2. **Visual consistency**: Are fallback flags resolved? Are image "
    "references present and plausible? Does the image_generation_report "
    "(if provided) indicate failures?\n"
    "3. **Text accuracy**: Is the text in slide_contents.json coherent, free "
    "of obvious errors, and does it read like professional presentation copy?\n\n"
    "Return your findings as a JSON object with keys:\n"
    "  - content_completeness: {score: 0-100, issues: [...]}\n"
    "  - visual_consistency:   {score: 0-100, issues: [...]}\n"
    "  - text_accuracy:        {score: 0-100, issues: [...]}\n"
    "  - overall_score: 0-100\n"
    "  - summary: string\n"
)


def _fresh_eyes_validation(workspace: JobWorkspace, llm_client) -> dict:
    """Run an LLM-based validation with isolated (fresh-eyes) context.

    The LLM receives ONLY the final artifacts — no accumulated conversation
    history from prior phases.
    """
    slide_contents = load_artifact(workspace, "slide_contents")

    # Build a minimal context containing only the artifacts needed
    context: dict = {
        "slide_contents": slide_contents,
        "final_pptx_exists": (workspace.root / "final.pptx").exists(),
    }

    image_report_path = workspace.artifact_path("image_generation_report")
    if image_report_path.exists():
        context["image_generation_report"] = load_artifact(workspace, "image_generation_report")

    # Deterministic fallback in case the LLM call fails
    deterministic_warnings: list[str] = []
    if context.get("image_generation_report"):
        deterministic_warnings.extend(context["image_generation_report"].get("warnings", []))
    if not context["final_pptx_exists"]:
        deterministic_warnings.append("final.pptx is missing")

    fallback = {
        "content_completeness": {"score": 70, "issues": []},
        "visual_consistency": {"score": 70, "issues": deterministic_warnings},
        "text_accuracy": {"score": 70, "issues": []},
        "overall_score": 70,
        "summary": "Deterministic fallback — LLM fresh-eyes validation was unavailable.",
    }

    prompt = (
        "Review the following PPT artifacts and provide your quality assessment.\n\n"
        f"slide_contents.json:\n```json\n{json.dumps(slide_contents, ensure_ascii=False, indent=2)[:12000]}\n```\n\n"
        f"final.pptx exists: {context['final_pptx_exists']}\n"
    )
    if "image_generation_report" in context:
        report_text = json.dumps(context["image_generation_report"], ensure_ascii=False, indent=2)[:4000]
        prompt += f"\nimage_generation_report.json:\n```json\n{report_text}\n```\n"

    logger.info("Running fresh-eyes LLM validation (no prior phase context)")

    from ppt_agent.llm.schemas import VERIFICATION_SCHEMA

    result = llm_client.generate_json(
        prompt=prompt,
        context={},  # Intentionally empty — no prior phase context
        system=_FRESH_EYES_SYSTEM,
        phase="verification",
        fallback=fallback,
        schema=VERIFICATION_SCHEMA,
    )

    # Ensure expected keys are present
    result.setdefault("content_completeness", {"score": 70, "issues": []})
    result.setdefault("visual_consistency", {"score": 70, "issues": []})
    result.setdefault("text_accuracy", {"score": 70, "issues": []})
    result.setdefault("overall_score", 70)
    result.setdefault("summary", "")

    return result


def run(workspace: JobWorkspace, force: bool = False, llm_client=None) -> Path:
    output = workspace.artifact_path("validation_report")
    if output.exists() and not force:
        return output

    slide_contents = load_artifact(workspace, "slide_contents")

    # Collect deterministic warnings (always run)
    warnings: list[str] = []
    image_report_path = workspace.artifact_path("image_generation_report")
    if image_report_path.exists():
        image_report = load_artifact(workspace, "image_generation_report")
        warnings.extend(image_report.get("warnings", []))
    if not (workspace.root / "final.pptx").exists():
        warnings.append("final.pptx is missing")

    report = validation_report(workspace.root.name, slide_contents, warnings)

    # Strict text-only assembly must be mechanically provable: no shape
    # movement/resizing and no typography drift relative to ingested zones.
    final_pptx = workspace.root / "final.pptx"
    template_zones_path = workspace.artifact_path("template_zones")
    if template_zones_path.exists():
        invariant_report = _strict_assembly_invariants(
            final_pptx,
            load_artifact(workspace, "template_zones"),
            slide_contents,
        )
        report["strict_assembly_invariants"] = invariant_report
        if invariant_report["applicable"] and not invariant_report["passed"]:
            report["status"] = "failed"
            message = (
                "Strict assembly invariant failure: "
                f"{len(invariant_report['issues'])} geometry/format issue(s)."
            )
            report["manual_review_items"].append(message)

    # ── Visual audit (PPTX structural check, no image rendering needed) ──
    if final_pptx.exists() and visual_audit_pptx is not None:
        try:
            visual_report = visual_audit_pptx(final_pptx)
            report["visual_audit"] = visual_report.to_dict()
            # Promote visual issues to main warnings
            for rec in visual_report.recommendations:
                warnings.append(f"[视觉检查] {rec}")
            logger.info(
                "Visual audit: score=%.1f, font_issues=%d, contrast_issues=%d, overflow_issues=%d",
                visual_report.aggregate_score,
                visual_report.total_font_issues,
                visual_report.total_contrast_issues,
                visual_report.total_overflow_issues,
            )
        except Exception as exc:
            logger.warning("Visual audit failed: %s", exc)
    elif visual_audit_pptx is None:
        logger.debug("Visual audit skipped — vision module not available")

    # If an LLM is available, augment with fresh-eyes validation
    if llm_client is not None:
        try:
            fresh_eyes = _fresh_eyes_validation(workspace, llm_client)
            report["fresh_eyes_validation"] = fresh_eyes
            logger.info(
                "Fresh-eyes validation complete: overall_score=%s",
                fresh_eyes.get("overall_score"),
            )
        except Exception as exc:
            logger.warning("Fresh-eyes LLM validation failed, using deterministic only: %s", exc)
    else:
        logger.info("No LLM client available — using deterministic verification only")

    return write_artifact(workspace, "validation_report", report)
