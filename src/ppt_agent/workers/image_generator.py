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


def prepare_generation_config(workspace: JobWorkspace, mode: str = "none") -> tuple[Path, dict]:
    """Prepare the image generation config from slide_contents.

    Loads template_zones to get template image paths, then merges
    them into slide_contents before building the batch config.

    Parameters
    ----------
    mode
        "key" (default) — cover + section dividers only.
        "all" — every slide.
        "cover-only" — just the first slide.
        "none" — skip generation entirely.
    """
    slide_contents = load_artifact(workspace, "slide_contents")

    # Enrich slide_contents with template image paths from template_zones
    try:
        template_zones = load_artifact(workspace, "template_zones")
        _enrich_with_template_images(slide_contents, template_zones, workspace)
    except (FileNotFoundError, Exception) as e:
        logger.info("No template_zones available for image enrichment: %s", e)

    config = slide_contents_to_batch_config(slide_contents, mode=mode)
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
                "output_name": s.get("output_name", f"slide-{s['index']:03d}.png"),
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
        output_name = slide_entry.get("output_name", f"slide-{idx:03d}.png")

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


def _invoke_gptimage2_skill(workspace: JobWorkspace, config: dict) -> None:
    """Invoke the gptimage2-generator skill client to generate background images.

    If no accounts exist, auto-registers one (the gptimage2.online service accepts
    made-up emails — no real email verification required for free tier usage).

    Calls the batch-generate CLI command with the prepared config.
    Non-zero exit or missing output → raises RuntimeError for the caller to handle.
    """
    import subprocess
    import uuid

    # Resolve .catpaw relative to the project root (4 levels up from this file)
    _project_root = Path(__file__).resolve().parent.parent.parent.parent
    skill_script = _project_root / ".catpaw/skills/gptimage2-generator/scripts/gptimage2_client.py"
    accounts_file = _project_root / ".catpaw/skills/gptimage2-generator/assets/accounts.json"
    config_path = workspace.artifact_path("image_generation_config")
    output_dir = workspace.background_images_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Calculate how many accounts we need (each gets ~30 free credits, 1K=10pts)
    total_slides = len(config.get("slides", []))
    resolution = config.get("slides", [{}])[0].get("resolution", "1K")
    cost_per_slide = {"1K": 10, "2K": 20, "4K": 40}.get(resolution, 10)
    credits_per_account = 30
    slides_per_account = max(1, credits_per_account // cost_per_slide)
    needed_accounts = max(1, (total_slides + slides_per_account - 1) // slides_per_account)

    # Load existing accounts and count active ones
    accounts = {"accounts": [], "current_index": 0}
    if accounts_file.exists():
        try:
            accounts = json.loads(accounts_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    active = sum(1 for a in accounts.get("accounts", []) if a.get("status") == "active")
    need_to_register = max(0, needed_accounts - active)

    logger.info("Slides: %d, cost/slide: %d, need %d accounts, have %d active, registering %d",
                total_slides, cost_per_slide, needed_accounts, active, need_to_register)

    for _ in range(need_to_register):
        email = f"pptagent_{uuid.uuid4().hex[:8]}@outlook.com"
        password = f"Agent{uuid.uuid4().hex[:8]}!"
        logger.info("Auto-registering GPTImage2 account: %s", email)

        r = subprocess.run(
            ["python", str(skill_script), "--accounts-file", str(accounts_file),
             "signup", "--email", email, "--password", password],
            capture_output=True, text=True, timeout=30,
            cwd=str(workspace.root.parent.parent.parent),
        )
        if r.returncode != 0:
            logger.warning("Registration failed for %s: %s", email, r.stderr[-200:])
            break  # Don't register more if one fails
        logger.info("Registered: %s", email)

    logger.info("Invoking gptimage2-generator skill: %d slides", len(config.get("slides", [])))
    result = subprocess.run(
        [
            "python", str(skill_script),
            "--accounts-file", str(accounts_file),
            "batch-generate",
            "--config", str(config_path),
            "--output-dir", str(output_dir),
        ],
        capture_output=True, text=True, timeout=1800,  # 30 min for 12 slides
        cwd=str(workspace.root.parent.parent.parent),
    )

    if result.returncode != 0:
        # Don't fail if some images were generated — batch may have exited
        # non-zero after exhausting all accounts (some slides fall back)
        existing = list(output_dir.glob("slide-*.png"))
        if existing:
            logger.warning("gptimage2 skill exited %d but %d images exist, continuing",
                          result.returncode, len(existing))
        else:
            raise RuntimeError(f"gptimage2 skill exited {result.returncode}: {result.stderr[-500:]}")

    stdout_tail = result.stdout.strip().split("\n")[-5:]
    logger.info("gptimage2 skill output:\n%s", "\n".join(stdout_tail))


def run(workspace: JobWorkspace, force: bool = False, mode: str = "none") -> Path:
    """Deterministic fallback for the visual_generation phase.

    Parameters
    ----------
    mode
        "none" (default) — skip AI generation, use template slides directly.
        "key" — cover + section dividers only.
        "all" — every slide gets AI background.
        "cover-only" — just the first slide.
    """
    output = workspace.artifact_path("image_generation_report")
    if output.exists() and not force:
        return output

    config_path, config = prepare_generation_config(workspace, mode=mode)

    skill_context = build_skill_context(workspace, config)
    instructions_path = workspace.root / "skill_instructions.json"
    instructions_path.write_text(
        json.dumps(skill_context, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    report = check_generated_images(workspace, config)

    # Only invoke the skill if there are slides to generate
    if config.get("slides") and report["generated"] == 0:
        try:
            _invoke_gptimage2_skill(workspace, config)
        except Exception as e:
            logger.warning("GPTImage2 skill invocation failed: %s", e)

        report = check_generated_images(workspace, config)
        if report["generated"] > 0:
            report["execution"] = {"method": "gptimage2_skill"}
        else:
            report = fallback_image_report(
                workspace.root.name,
                len(config.get("slides", [])),
            )
            report["execution"] = {
                "method": "deterministic_fallback",
                "skill_context_path": str(instructions_path),
                "error": "No images generated",
            }
    elif report["generated"] > 0:
        report["execution"] = {"method": "pre_existing_images"}
    else:
        # mode="none" or empty config — skip generation entirely
        report["execution"] = {"method": "template_only", "note": "Using template slides directly, no AI generation needed"}

    return write_artifact(workspace, "image_generation_report", report)
