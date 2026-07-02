from __future__ import annotations

from pathlib import Path

from ppt_agent.coordinator.event_bus import emit_event
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.workers import content_mapper, design_director, document_analyst, image_generator, outline_generator, ppt_assembler, ppt_verifier, template_matcher

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

UNTIL_ALIASES = {"content-review": "content_mapping", "final": "verification"}
FROM_ALIASES = {"visual-generation": "visual_generation", "assembly": "ppt_assembly"}


def run_workflow(job: Path | JobWorkspace, until: str | None = None, start_from: str | None = None, force: bool = False) -> list[Path]:
    workspace = job if isinstance(job, JobWorkspace) else JobWorkspace(Path(job))
    workspace.ensure()
    end_phase = UNTIL_ALIASES.get(until or "final", until or "verification")
    start_phase = FROM_ALIASES.get(start_from or PHASES[0], start_from or PHASES[0])
    start_idx = PHASES.index(start_phase)
    end_idx = PHASES.index(end_phase)
    outputs: list[Path] = []
    for phase in PHASES[start_idx : end_idx + 1]:
        emit_event(workspace.root, phase, "started", f"Starting {phase}")
        output = PHASE_TO_WORKER[phase](workspace, force=force)
        if isinstance(output, list):
            outputs.extend(output)
        elif output is not None:
            outputs.append(output)
        emit_event(workspace.root, phase, "completed", f"Completed {phase}", artifact_path=str(output) if isinstance(output, Path) else None)
    return outputs
