"""Image generation worker — data preparation layer for full-page backgrounds.

The new flow generates FULL-PAGE background images via img2img:
1. Load slide_contents + template_zones
2. For each slide: build a prompt from content + use template image as reference
3. GPTImage2 generates a full-page 16:9 background image
4. The background image is later overlaid with text in ppt_assembly

This module handles:
    1. Converting slide_contents → image_generation_config (batch config)
    2. Writing skill_instructions.json (structured context for Worker Agent)
    3. Checking generated background images
    4. Writing image_generation_report

The ACTUAL image generation is driven by the WorkerAgent's LLM, which reads
the gptimage2-generator SKILL.md and autonomously decides how to invoke
the Skill's scripts.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ppt_agent.coordinator.phase_state import load_artifact, write_artifact
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.models.image_generation import fallback_image_report
from ppt_agent.skills.adapters.gptimage2 import slide_contents_to_batch_config

logger = logging.getLogger(__name__)


def prepare_generation_config(workspace: JobWorkspace) -> tuple[Path, dict]:
    """Prepare the image generation config from slide_contents.

    Loads template_zones to get template image paths, then merges
    them into slide_contents before building the batch config.
    """
    slide_contents = load_artifact(workspace, "slide_contents")

    # Enrich slide_contents with template image paths from template_zones
    try:
        template_zones = load_artifact(workspace, "template_zones")
        _enrich_with_template_images(slide_contents, template_zones, workspace)
    except (FileNotFoundError, Exception) as e:
        logger.info("No template_zones available for image enrichment: %s", e)

    config = slide_contents_to_batch_config(slide_contents)
    config_path = write_artifact(workspace, "image_generation_config", config)
    return config_path, config


def _enrich_with_template_images(
    slide_contents: dict,
    template_zones: dict,
    workspace: JobWorkspace,
) -> None:
    """Add template_image paths to slide_contents entries.

    The template_zones artifact has per-slide template image paths.
    We resolve them to absolute paths and inject into slide_contents.
    """
    tpl_slides = {s["index"]: s for s in template_zones.get("slides", [])}

    for slide in slide_contents.get("slides", []):
        idx = slide.get("slide_index", slide.get("index", -1))
        tpl_slide = tpl_slides.get(idx, {})
        tpl_image = tpl_slide.get("template_image")

        if tpl_image and not slide.get("template_image"):
            # Resolve to absolute path
            tpl_path = Path(tpl_image)
            if not tpl_path.is_absolute():
                tpl_path = workspace.root / tpl_image
            if tpl_path.exists():
                slide["template_image"] = str(tpl_path)
            else:
                logger.debug("Template image not found: %s", tpl_path)


def build_skill_context(workspace: JobWorkspace, config: dict) -> dict:
    """Build structured context for the WorkerAgent's LLM.

    This is CONTEXT — the agent reads this + SKILL.md and reasons its own plan.
    """
    slides = config.get("slides", [])

    return {
        "task": "background_image_generation",
        "skill_name": "gptimage2-generator",
        "workspace_root": str(workspace.root),
        "config_path": str(workspace.artifact_path("image_generation_config")),
        "output_dir": str(workspace.background_images_dir),
        "slide_count": len(slides),
        "generation_mode": "full_page_background",
        "slides_needing_images": [
            {
                "index": s["index"],
                "prompt": s["prompt"],
                "has_reference": s.get("reference_image") is not None,
                "reference_image": s.get("reference_image"),
                "output_name": s.get("output_name", f"slide_{s['index']:02d}.png"),
            }
            for s in slides
        ],
        "notes": (
            "Generate FULL-PAGE 16:9 background images for each slide. "
            "When a reference_image is provided, use img2img (--reference) "
            "to maintain the template's visual style. When no reference is "
            "available, use text2img with the prompt. "
            "Output images go to the background_images/ directory. "
            "Read the Skill documentation (SKILL.md) for command details."
        ),
    }


def check_generated_images(workspace: JobWorkspace, config: dict) -> dict:
    """Check which background images were generated after execution.

    Checks BOTH background_images/ (new flow) and generated_slides/ (legacy).
    """
    slides = config.get("slides", [])
    job_id = workspace.root.name

    slide_results = []
    total_generated = 0
    total_fallback = 0

    for slide_entry in slides:
        idx = slide_entry["index"]
        output_name = slide_entry.get("output_name", f"slide_{idx:02d}.png")

        # Check new location first, then legacy
        bg_path = workspace.background_images_dir / output_name
        legacy_path = workspace.generated_slides_dir / output_name

        if bg_path.exists():
            slide_results.append({
                "slide_index": idx,
                "status": "generated",
                "image_path": str(bg_path.relative_to(workspace.root)),
                "prompt_used": slide_entry.get("prompt", ""),
            })
            total_generated += 1
        elif legacy_path.exists():
            slide_results.append({
                "slide_index": idx,
                "status": "generated",
                "image_path": str(legacy_path.relative_to(workspace.root)),
                "prompt_used": slide_entry.get("prompt", ""),
            })
            total_generated += 1
        else:
            slide_results.append({
                "slide_index": idx,
                "status": "fallback_used",
                "reason": "Background image not found",
                "prompt_used": slide_entry.get("prompt", ""),
            })
            total_fallback += 1

    if total_fallback == 0:
        overall_status = "succeeded"
    elif total_generated > 0:
        overall_status = "succeeded_with_fallbacks"
    else:
        overall_status = "all_fallback"

    return {
        "job_id": job_id,
        "status": overall_status,
        "total_slides": len(slides),
        "generated": total_generated,
        "fallback": total_fallback,
        "slides": slide_results,
        "warnings": [] if total_fallback == 0 else [
            f"{total_fallback} slide(s) have no background image — will use solid color.",
        ],
    }


def run(workspace: JobWorkspace, force: bool = False) -> Path:
    """Deterministic fallback for the visual_generation phase."""
    output = workspace.artifact_path("image_generation_report")
    if output.exists() and not force:
        return output

    config_path, config = prepare_generation_config(workspace)

    skill_context = build_skill_context(workspace, config)
    instructions_path = workspace.root / "skill_instructions.json"
    instructions_path.write_text(
        json.dumps(skill_context, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    report = check_generated_images(workspace, config)

    if report["generated"] > 0:
        report["execution"] = {"method": "pre_existing_images"}
    else:
        logger.info(
            "No LLM available for autonomous Skill execution. "
            "skill_instructions.json written at %s for manual execution.",
            instructions_path,
        )
        report = fallback_image_report(
            workspace.root.name,
            len(config.get("slides", [])),
        )
        report["execution"] = {
            "method": "deterministic_fallback",
            "skill_context_path": str(instructions_path),
        }

    return write_artifact(workspace, "image_generation_report", report)
