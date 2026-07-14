"""Workflow orchestration — the main pipeline driver.

Two execution modes:
1. AGENT MODE (with LLM): The CoordinatorAgent runs in a think→act→observe
   loop, spawning WorkerAgents for each phase. Each worker also runs its own
   agent loop with self-correction capability.

2. DETERMINISTIC MODE (without LLM): Falls back to the original linear
   for-loop execution, calling each worker function directly.

Both modes integrate the three-layer memory system, event bus with pub/sub,
session summaries, and dream consolidation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ppt_agent.context.memory_layers import build_memory_context, ensure_memory_files
from ppt_agent.context.session_summary import append_phase_summary
from ppt_agent.context.dream import run_dream_task
from ppt_agent.coordinator.event_bus import emit_event, EventBus, EventType, LoggingConsumer
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.runtime.agent_loop import StopReason
from ppt_agent.runtime.task_manager import TaskManager
from ppt_agent.runtime.task_types import TaskType, TaskStatus
from ppt_agent.skills.loader import SkillLoader
from ppt_agent.workers import content_mapper, design_director, document_analyst, image_generator, outline_generator, ppt_assembler, ppt_verifier, template_matcher

logger = logging.getLogger(__name__)

PHASES = [
    "document_analysis",
    "outline_generation",
    "template_matching",
    "design_planning",
    "content_mapping",
    "visual_generation",
    "ppt_assembly",
    "verification",
]

PHASE_TO_WORKER = {
    "document_analysis": document_analyst.run,
    "outline_generation": outline_generator.run,
    "template_matching": template_matcher.run,
    "design_planning": design_director.run,
    "content_mapping": content_mapper.run,
    "visual_generation": image_generator.run,
    "ppt_assembly": ppt_assembler.run,
    "verification": ppt_verifier.run,
}

# Phases that accept an llm_client parameter
_LLM_PHASES = {
    "document_analysis",
    "outline_generation",
    "template_matching",
    "design_planning",
    "content_mapping",
    "verification",
}

# Extra kwargs to pass to specific phases
_PHASE_EXTRA_KWARGS: dict[str, dict] = {
    "visual_generation": {"mode": "all"},
}

# Phase → skill name mapping (from config/dispatcher.yml)
_PHASE_SKILL = {
    "outline_generation": "ppt-outline-generator",
    "template_matching": "ppt-template-matcher",
    "design_planning": "ppt-design-director",
    "content_mapping": "ppt-content-mapper",
    "visual_generation": "ppt-image-layer",
    "ppt_assembly": "ppt-assembler",
}

UNTIL_ALIASES = {"content-review": "content_mapping", "final": "verification"}
FROM_ALIASES = {"visual-generation": "visual_generation", "assembly": "ppt_assembly"}


def _check_review_gate(
    workspace: JobWorkspace,
    bus: EventBus,
    force: bool,
    outputs: list[Path],
) -> bool:
    """Check the user-review gate after content_mapping.

    Emits a user_input_request event, writes a review_pending.json marker,
    and inspects slide_contents.json for ``review_status: approved``.

    Returns True if the pipeline may proceed, False if it must stop.
    """
    slide_contents_path = workspace.artifact_path("slide_contents")

    # Write the review-pending marker so external tooling can detect it
    marker_path = workspace.root / "review_pending.json"
    marker = {
        "slide_contents_path": str(slide_contents_path),
        "status": "pending_review",
    }
    marker_path.write_text(json.dumps(marker, indent=2), encoding="utf-8")

    # Emit an event so UI / CLI consumers know we are waiting
    bus.emit(
        EventType.USER_INPUT_REQUEST,
        phase="content_mapping",
        message="slide_contents.json is ready for user review. "
                "Set review_status to 'approved' in the file to continue.",
        artifact_path=str(slide_contents_path),
    )

    if force:
        logger.info("force=True — skipping review gate, proceeding with pipeline.")
        return True

    # Read the slide_contents and check for approval
    if slide_contents_path.exists():
        try:
            contents = json.loads(slide_contents_path.read_text(encoding="utf-8"))
            if contents.get("review_status") == "approved":
                logger.info("slide_contents.json review_status is 'approved' — proceeding.")
                return True
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read slide_contents.json for review check: %s", exc)

    logger.info(
        "Pipeline paused after content_mapping: user review is required. "
        "Approve slide_contents.json (set review_status to 'approved') "
        "or re-run with force=True to continue."
    )
    return False


def run_workflow(
    job: Path | JobWorkspace,
    until: str | None = None,
    start_from: str | None = None,
    force: bool = False,
    model_profile: str | None = None,
) -> list[Path]:
    """Run the PPT generation pipeline.

    When a model_profile is provided, uses the full agent architecture:
    CoordinatorAgent → WorkerAgents, each running think→act→observe loops.

    Without a model_profile, falls back to deterministic linear execution.
    """
    workspace = job if isinstance(job, JobWorkspace) else JobWorkspace(Path(job))
    workspace.ensure()
    end_phase = UNTIL_ALIASES.get(until or "final", until or "verification")
    start_phase = FROM_ALIASES.get(start_from or PHASES[0], start_from or PHASES[0])
    start_idx = PHASES.index(start_phase)
    end_idx = PHASES.index(end_phase)
    active_phases = PHASES[start_idx : end_idx + 1]

    # Initialize memory system
    ensure_memory_files(workspace.root)

    # Initialize event bus with logging consumer
    bus = EventBus(workspace.root, auto_file_consumer=True)
    bus.subscribe(LoggingConsumer())

    # Create LLM client if a model profile is specified
    llm_client = None
    agent_mode = False
    if model_profile:
        # "agent:<profile>" → full agent mode (Coordinator + Worker agent loops)
        if model_profile.startswith("agent:"):
            agent_mode = True
            model_profile = model_profile[len("agent:"):]
        llm_client = _create_llm_client(model_profile, workspace.root)

    if llm_client is not None:
        if agent_mode:
            # ── AGENT MODE ──
            # Deterministic scheduler + WorkerAgent per phase.
            # For-loop iterates phases in order. Each phase gets a
            # WorkerAgent with its own agent loop for decision-making.
            # Tools inside the agent loop call worker functions (no llm_client).
            return _run_agent_workers(
                workspace, llm_client, bus, active_phases, force,
            )
        else:
            # ── LLM-AUGMENTED DETERMINISTIC MODE ──
            # Each worker calls the LLM for intelligent analysis but the
            # pipeline runs linearly — fast and reliable.
            return _run_llm_augmented_workflow(
                workspace, llm_client, bus, active_phases, force,
            )
    else:
        # ── DETERMINISTIC MODE: Linear for-loop (backward compatible) ──
        return _run_deterministic_workflow(
            workspace, bus, active_phases, force,
        )


def _run_agent_workers(
    workspace: JobWorkspace,
    llm_client,
    bus: EventBus,
    phases: list[str],
    force: bool,
) -> list[Path]:
    """Deterministic scheduler + WorkerAgent per phase.

    No CoordinatorAgent — just a for-loop. Each phase gets its own
    WorkerAgent with agent loop for decision-making. Tools inside
    the agent loop bridge to worker functions (no internal llm_client).
    """
    from ppt_agent.coordinator.capabilities import get_capability
    from ppt_agent.coordinator.worker_agent import WorkerAgent

    skill_loader = SkillLoader(root=Path(__file__).resolve().parent.parent.parent.parent / ".catpaw" / "skills")
    memory_context = build_memory_context(workspace.root)
    if memory_context:
        llm_client._memory_context = memory_context

    outputs: list[Path] = []

    for phase in phases:
        bus.emit(EventType.PHASE_STARTED, phase=phase, message=f"Starting {phase} (agent)")

        capability = get_capability(phase)
        if capability is None:
            logger.warning("No capability for phase %s, skipping", phase)
            continue

        _inject_phase_skill(skill_loader, llm_client, phase)

        # Load the skill content for this phase so the WorkerAgent's
        # system prompt can include the full SKILL.md documentation.
        skill_content = None
        if capability.skill_name and skill_loader:
            skill_content = skill_loader.load_skill(capability.skill_name)

        worker = WorkerAgent(
            workspace=workspace,
            phase_or_capability=capability,
            llm_client=llm_client,
            force=force,
            skill_content=skill_content,
        )

        # Build the task prompt.  For skill-autonomous phases (visual_generation)
        # we MUST give the agent an explicit command to execute on its first turn,
        # otherwise it tends to reason about the skill doc indefinitely without
        # ever calling run_shell_command.
        if phase == "visual_generation" and capability.skill_name:
            config_path = workspace.artifact_path("image_generation_config")
            output_dir = workspace.background_images_dir
            _project_root = Path(__file__).resolve().parent.parent.parent.parent
            skill_script = _project_root / ".catpaw" / "skills" / "gptimage2-generator" / "scripts" / "gptimage2_client.py"
            accounts_file = _project_root / ".catpaw" / "skills" / "gptimage2-generator" / "assets" / "accounts.json"
            output_dir.mkdir(parents=True, exist_ok=True)

            shell_cmd = (
                f"python \"{skill_script}\" "
                f"--accounts-file \"{accounts_file}\" "
                f"batch-generate "
                f"--config \"{config_path}\" "
                f"--output-dir \"{output_dir}\""
            )
            task_prompt = (
                f"Execute the visual_generation capability.\n\n"
                f"## MANDATORY FIRST ACTION\n"
                f"On your VERY FIRST turn, call run_shell_command with:\n"
                f"  command: {shell_cmd}\n"
                f"  timeout: 600\n"
                f"  cwd: \"{_project_root}\"\n\n"
                f"DO NOT reason first. DO NOT read the skill doc first. "
                f"EXECUTE the command NOW. The skill handles login, generation, "
                f"and account switching automatically.\n\n"
                f"After the command completes, check batch_report.json in "
                f"\"{output_dir}\" and write the image_generation_report."
            )
        else:
            task_prompt = f"Execute the {phase} capability."

        result = worker.run(task_prompt=task_prompt)

        if result.stop_reason == StopReason.COMPLETED:
            bus.emit(EventType.PHASE_COMPLETED, phase=phase, message=f"Completed {phase}")
        else:
            logger.warning("Phase %s ended with %s", phase, result.stop_reason.value)

        append_phase_summary(
            workspace.root, phase,
            summary=f"Agent phase {phase}: {result.stop_reason.value}",
            artifact_name=str(result.final_output) if result.final_output else None,
        )

        if phase == "content_mapping":
            if not _check_review_gate(workspace, bus, force, outputs):
                break

    # Collect artifacts
    _collect_outputs(workspace, phases, outputs)

    if "verification" in phases and llm_client is not None:
        try:
            insights = run_dream_task(workspace.root, llm_client)
            if insights:
                logger.info("Dream task saved %d insights", len(insights))
        except Exception as e:
            logger.warning("Dream task failed: %s", e)

    return outputs


def _collect_outputs(workspace, phases, outputs):
    """Scan workspace for expected artifacts and append to outputs."""
    _PHASE_ARTIFACTS = {
        "document_analysis": ["source_summary"],
        "outline_generation": ["outline"],
        "template_matching": ["selected_template", "template_meta", "template_zones"],
        "design_planning": ["slide_design_plan"],
        "content_mapping": ["slide_contents"],
        "visual_generation": ["image_generation_report"],
        "verification": ["validation_report"],
    }
    for phase in phases:
        for name in _PHASE_ARTIFACTS.get(phase, []):
            p = workspace.artifact_path(name)
            if p.exists() and p not in outputs:
                outputs.append(p)
    final_pptx = workspace.root / "final.pptx"
    if final_pptx.exists():
        outputs.append(final_pptx)


def _run_agent_workflow(
    workspace: JobWorkspace,
    llm_client,
    bus: EventBus,
    phases: list[str],
    force: bool,
) -> list[Path]:
    """Run the pipeline using the full agent architecture.

    The CoordinatorAgent runs in a think→act→observe loop:
    1. It reasons about which phase to execute next
    2. Spawns a WorkerAgent for that phase
    3. The WorkerAgent runs its own loop with self-correction
    4. Results flow back via mailbox
    5. Coordinator decides the next step
    """
    from ppt_agent.coordinator.coordinator_agent import CoordinatorAgent

    task_manager = TaskManager()
    skill_loader = SkillLoader(root=Path(__file__).resolve().parent.parent.parent.parent / ".catpaw" / "skills")

    # Inject memory context into LLM client
    memory_context = build_memory_context(workspace.root)
    if memory_context:
        llm_client._memory_context = memory_context

    # Create the coordinator agent
    coordinator = CoordinatorAgent(
        workspace=workspace,
        llm_client=llm_client,
        task_manager=task_manager,
        event_bus=bus,
        skill_loader=skill_loader,
        phases=phases,
        force=force,
    )

    # Create the coordinator's own task
    coord_task = task_manager.create(
        TaskType.LOCAL_WORKFLOW,
        description="PPT generation coordinator",
        task_id="coordinator",
    )
    task_manager.transition(coord_task.task_id, TaskStatus.RUNNING)

    # Run the coordinator's agent loop
    phase_list = ", ".join(phases)
    force_note = (
        " force=True is set, so skip the review gate and proceed."
        if force
        else (
            " After content_mapping completes, check whether slide_contents.json "
            "contains '\"review_status\": \"approved\"'. If it does NOT, stop the "
            "pipeline and report that user review is required before proceeding."
        )
    )
    result = coordinator.run(
        task_prompt=(
            f"Execute the PPT generation pipeline. Available capabilities: {phase_list}. "
            f"Decide which capabilities to invoke and in what order. "
            f"Respect dependency rules (e.g. outline_generation needs source_summary from document_analysis). "
            f"Use spawn_agent for each capability. Independent phases may run in parallel. "
            f"After all phases complete, use synthesize_output to produce the final result."
            f"{force_note}"
        ),
    )

    # Mark coordinator task as done
    if result.stop_reason.value == "completed":
        task_manager.transition(coord_task.task_id, TaskStatus.COMPLETED, result=result.final_output)
    else:
        task_manager.transition(coord_task.task_id, TaskStatus.FAILED, error=result.error)

    # Record session summaries for completed phases
    for phase in coordinator.completed_phases:
        worker_info = coordinator.worker_results.get(phase, {})
        append_phase_summary(
            workspace.root, phase,
            summary=f"Phase {phase} completed via agent loop ({worker_info.get('turns', '?')} turns).",
            artifact_name=worker_info.get("output"),
        )

    # Run dream consolidation
    if "verification" in coordinator.completed_phases:
        try:
            insights = run_dream_task(workspace.root, llm_client)
            if insights:
                logger.info("Dream task saved %d insights to memory.md", len(insights))
        except Exception as e:
            logger.warning("Dream task failed: %s", e)

    # Collect output paths — scan workspace for expected artifacts rather
    # than relying on worker_result, which stores task metadata not file paths.
    outputs: list[Path] = []
    # Phase → expected artifact names (same order as PHASES)
    _PHASE_ARTIFACTS: dict[str, list[str]] = {
        "document_analysis": ["source_summary"],
        "outline_generation": ["outline"],
        "template_matching": ["selected_template", "template_meta", "template_zones"],
        "design_planning": ["slide_design_plan"],
        "content_mapping": ["slide_contents"],
        "visual_generation": ["image_generation_report"],
        "ppt_assembly": [],  # final.pptx handled separately
        "verification": ["validation_report"],
    }
    for phase in coordinator.completed_phases:
        for name in _PHASE_ARTIFACTS.get(phase, []):
            p = workspace.artifact_path(name)
            if p.exists():
                outputs.append(p)
    # Check for final.pptx
    pptx = workspace.root / "final.pptx"
    if pptx.exists():
        outputs.append(pptx)

    return outputs


def _run_llm_augmented_workflow(
    workspace: JobWorkspace,
    llm_client,
    bus: EventBus,
    phases: list[str],
    force: bool,
) -> list[Path]:
    """Run the pipeline linearly but with LLM-augmented workers.

    This is the middle ground: the pipeline is still sequential (no agent loops),
    but workers that support LLM (document_analysis, outline_generation,
    design_planning, content_mapping) receive the llm_client for intelligent
    analysis. Used when the LLM provider is "fake" or otherwise not capable
    of producing structured agent tool-call JSON.
    """
    skill_loader = SkillLoader(root=Path(__file__).resolve().parent.parent.parent.parent / ".catpaw" / "skills")

    # Inject memory context into LLM client
    memory_context = build_memory_context(workspace.root)
    if memory_context:
        llm_client._memory_context = memory_context

    outputs: list[Path] = []
    for phase in phases:
        bus.emit(EventType.PHASE_STARTED, phase=phase, message=f"Starting {phase}")

        # Load skill content for this phase (progressive disclosure)
        _inject_phase_skill(skill_loader, llm_client, phase)

        # Execute the worker (with or without LLM)
        extra = _PHASE_EXTRA_KWARGS.get(phase, {})
        if phase in _LLM_PHASES and llm_client is not None:
            output = PHASE_TO_WORKER[phase](workspace, force=force, llm_client=llm_client, **extra)
        else:
            output = PHASE_TO_WORKER[phase](workspace, force=force, **extra)

        if isinstance(output, list):
            outputs.extend(output)
        elif output is not None:
            outputs.append(output)

        # Record phase summary to session.md
        artifact_name = output.name if isinstance(output, Path) else None
        append_phase_summary(
            workspace.root, phase,
            summary=f"Phase {phase} completed.",
            artifact_name=artifact_name,
        )

        bus.emit(
            EventType.PHASE_COMPLETED, phase=phase,
            message=f"Completed {phase}",
            artifact_path=str(output) if isinstance(output, Path) else None,
        )

        # Review gate: pause after content_mapping for user approval
        if phase == "content_mapping":
            if not _check_review_gate(workspace, bus, force, outputs):
                return outputs

    # Run dream consolidation at end of full workflow
    if "verification" in phases and llm_client is not None:
        try:
            insights = run_dream_task(workspace.root, llm_client)
            if insights:
                logger.info("Dream task saved %d insights to memory.md", len(insights))
        except Exception as e:
            logger.warning("Dream task failed: %s", e)

    return outputs


def _run_deterministic_workflow(
    workspace: JobWorkspace,
    bus: EventBus,
    phases: list[str],
    force: bool,
) -> list[Path]:
    """Fallback: run the pipeline as a linear for-loop (no LLM, no agent loops).

    This preserves full backward compatibility with the original implementation.
    """
    skill_loader = SkillLoader(root=Path(__file__).resolve().parent.parent.parent.parent / ".catpaw" / "skills")

    outputs: list[Path] = []
    for phase in phases:
        bus.emit(EventType.PHASE_STARTED, phase=phase, message=f"Starting {phase}")

        # Execute the worker
        output = PHASE_TO_WORKER[phase](workspace, force=force)

        if isinstance(output, list):
            outputs.extend(output)
        elif output is not None:
            outputs.append(output)

        # Record phase summary to session.md
        artifact_name = output.name if isinstance(output, Path) else None
        append_phase_summary(
            workspace.root, phase,
            summary=f"Phase {phase} completed.",
            artifact_name=artifact_name,
        )

        bus.emit(
            EventType.PHASE_COMPLETED, phase=phase,
            message=f"Completed {phase}",
            artifact_path=str(output) if isinstance(output, Path) else None,
        )

        # Review gate: pause after content_mapping for user approval
        if phase == "content_mapping":
            if not _check_review_gate(workspace, bus, force, outputs):
                return outputs

    return outputs


def _create_llm_client(profile: str, job_root: Path):
    """Create an LLM client from the model profile."""
    try:
        from ppt_agent.llm.client import LLMClient
        client = LLMClient.from_config(profile=profile, job_root=job_root)
        logger.info("LLM client created: provider=%s, model=%s", client.provider.name, client.provider.config.text_model)
        return client
    except Exception as e:
        logger.warning("Failed to create LLM client for profile '%s': %s. Falling back to deterministic mode.", profile, e)
        return None


def _inject_phase_skill(skill_loader: SkillLoader, llm_client, phase: str) -> None:
    """Load and inject skill content into the LLM client for this phase.

    This is the Phase 2 progressive disclosure: full SKILL.md content
    is loaded on demand and stored in the client's skill context.
    """
    if llm_client is None:
        return

    skill_name = _PHASE_SKILL.get(phase)
    if not skill_name:
        return

    skill_content = skill_loader.load_skill(skill_name)
    if skill_content:
        # Store skill context on the client for the current phase
        if not hasattr(llm_client, '_skill_contexts'):
            llm_client._skill_contexts = {}
        llm_client._skill_contexts[phase] = {
            "skill_name": skill_name,
            "content": skill_content,
        }
        logger.info("Injected skill '%s' for phase '%s'", skill_name, phase)
