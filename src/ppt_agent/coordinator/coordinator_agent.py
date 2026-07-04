"""CoordinatorAgent — the pure orchestration agent.

As per AGENT_ARCHITECTURE.md §9.2:
    - The Coordinator NEVER directly manipulates files, executes code, or searches.
    - It has EXACTLY 4 tools: AgentTool, TaskStopTool, SendMessageTool, SyntheticOutput.
    - It follows the four-phase structured workflow: Research → Synthesis → Implementation → Verification.
    - It runs in a think→act→observe loop, using LLM reasoning to decide
      which sub-agent to spawn next, when to collect results, and when to stop.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ppt_agent.context.memory_layers import build_memory_context
from ppt_agent.coordinator.event_bus import EventBus, EventType, LoggingConsumer
from ppt_agent.llm.json_repair import repair_json
from ppt_agent.llm.messages import LLMResult
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.runtime.agent_loop import (
    AgentLoop,
    AgentLoopResult,
    StopReason,
    ToolCall,
    ToolResult,
)
from ppt_agent.runtime.mailbox import Mailbox, MessagePriority
from ppt_agent.runtime.task_manager import TaskManager
from ppt_agent.runtime.task_types import TaskStatus, TaskType
from ppt_agent.skills.loader import SkillLoader

logger = logging.getLogger(__name__)

# The eight PPT pipeline phases
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

# Phase → skill mapping for progressive disclosure
_PHASE_SKILL = {
    "outline_generation": "ppt-outline-generator",
    "template_matching": "ppt-template-matcher",
    "design_planning": "ppt-design-director",
    "content_mapping": "ppt-content-mapper",
    "visual_generation": "ppt-image-layer",
    "ppt_assembly": "ppt-assembler",
}


class CoordinatorAgent(AgentLoop):
    """Pure orchestrator agent with exactly 4 tools.

    The Coordinator decomposes the PPT generation task into phases,
    spawns WorkerAgents for each phase, collects results via mailbox,
    and synthesizes the final output.
    """

    def __init__(
        self,
        workspace: JobWorkspace,
        llm_client,
        *,
        task_manager: TaskManager | None = None,
        event_bus: EventBus | None = None,
        skill_loader: SkillLoader | None = None,
        phases: list[str] | None = None,
        force: bool = False,
        max_turns: int = 30,
    ) -> None:
        super().__init__(
            agent_id="coordinator",
            llm_client=llm_client,
            max_turns=max_turns,
            max_consecutive_errors=5,
        )
        self.workspace = workspace
        self.task_manager = task_manager or TaskManager()
        self.event_bus = event_bus
        self.skill_loader = skill_loader or SkillLoader(root=Path(".catpaw/skills"))
        self.phases = phases or PHASES
        self.force = force

        # Mailbox for receiving results from workers
        self.mailbox = Mailbox(workspace.root / ".mailbox" / "coordinator.jsonl")

        # Track spawned workers and their results
        self._worker_results: dict[str, Any] = {}
        self._completed_phases: list[str] = []

    def build_system_prompt(self) -> str:
        """Build the Coordinator's system prompt."""
        memory_context = build_memory_context(self.workspace.root)
        skill_catalog = self.skill_loader.get_catalog_summary() if self.skill_loader else ""

        return f"""You are the Coordinator agent for a PPT generation pipeline.

## Your Role
You are a PURE ORCHESTRATOR. You never directly manipulate files, execute code,
or perform searches. Your sole responsibility is:
1. Decompose the PPT generation task into phases
2. Spawn specialized WorkerAgents for each phase
3. Collect and validate their results
4. Synthesize the final output

## Four-Phase Structured Workflow
You follow this disciplined framework:
1. RESEARCH: Spawn document_analysis worker to understand input materials
2. SYNTHESIS: Spawn outline_generation, template_matching, design_planning workers
   to form the plan (outline + template + design)
3. IMPLEMENTATION: Spawn content_mapping, visual_generation, ppt_assembly workers
   to create the actual PPT
4. VERIFICATION: Spawn verification worker with "fresh eyes" to validate the result

## PPT Pipeline Phases (execute in order)
{json.dumps(self.phases, indent=2)}

## Completed Phases
{json.dumps(self._completed_phases, indent=2)}

## Worker Results So Far
{json.dumps(self._worker_results, indent=2, default=str)}

## Available Skills (Phase 1 manifest — request full content via spawn_worker)
{skill_catalog}

{f'## Agent Memory{chr(10)}{memory_context}' if memory_context else ''}

## Important Rules
- Spawn workers ONE PHASE AT A TIME, in order
- Wait for each worker to complete before spawning the next
- After all phases complete, use synthesize_output to produce the final result
- If a worker reports errors, you may re-spawn it or adjust parameters
- The force flag is {'ON' if self.force else 'OFF'} (skip existing artifacts: {'no' if self.force else 'yes'})
"""

    def get_available_tools(self) -> list[dict]:
        """Return exactly 4 coordinator tools."""
        return [
            {
                "name": "spawn_worker",
                "description": (
                    "Spawn a WorkerAgent for a specific pipeline phase. "
                    "The worker runs autonomously in its own think→act→observe loop, "
                    "then reports results back via mailbox."
                ),
                "parameters": {
                    "phase": "The pipeline phase to execute (e.g., 'document_analysis', 'outline_generation')",
                    "instructions": "Optional additional instructions for the worker",
                },
            },
            {
                "name": "stop_worker",
                "description": "Stop a running worker agent by its task ID.",
                "parameters": {
                    "task_id": "The task ID of the worker to stop",
                },
            },
            {
                "name": "send_message",
                "description": "Send a message to a running worker agent via mailbox.",
                "parameters": {
                    "recipient": "The agent ID to send to",
                    "message": "The message content",
                },
            },
            {
                "name": "synthesize_output",
                "description": (
                    "Produce the final synthesized output after all phases are complete. "
                    "Call this when all pipeline phases have finished successfully."
                ),
                "parameters": {
                    "summary": "A brief summary of the overall result",
                    "warnings": "Any warnings or issues encountered during the pipeline",
                },
            },
        ]

    def execute_tool(self, tool_call: ToolCall) -> ToolResult:
        """Execute one of the 4 coordinator tools."""
        name = tool_call.tool_name
        args = tool_call.arguments

        if name == "spawn_worker":
            return self._spawn_worker(
                phase=args.get("phase", ""),
                instructions=args.get("instructions", ""),
            )
        elif name == "stop_worker":
            return self._stop_worker(task_id=args.get("task_id", ""))
        elif name == "send_message":
            return self._send_message(
                recipient=args.get("recipient", ""),
                message=args.get("message", ""),
            )
        elif name == "synthesize_output":
            return self._synthesize_output(
                summary=args.get("summary", ""),
                warnings=args.get("warnings", []),
            )
        else:
            return ToolResult(
                call_id=tool_call.call_id,
                output=None,
                success=False,
                error=f"Unknown tool: {name}. Available: spawn_worker, stop_worker, send_message, synthesize_output",
            )

    def parse_llm_response(self, result: LLMResult) -> tuple[str, list[ToolCall], bool]:
        """Parse LLM response to extract tool calls or done signal."""
        text = result.text

        # Try to extract JSON from the response
        try:
            data, _warnings = repair_json(text)
        except Exception:
            # No JSON found — treat as pure reasoning text
            return text, [], False

        if not isinstance(data, dict):
            return text, [], False

        # Check for done signal
        if data.get("done") is True:
            return text, [], True

        # Check for tool call
        tool_name = data.get("tool")
        if tool_name:
            tc = ToolCall(
                tool_name=tool_name,
                arguments=data.get("arguments", {}),
                call_id=f"tc_{len(self.turns) + 1}",
            )
            return text, [tc], False

        # JSON but no tool call or done signal
        return text, [], False

    def on_turn_complete(self, turn) -> None:
        """Emit events after each turn."""
        if self.event_bus:
            for tc in turn.tool_calls:
                if tc.tool_name == "spawn_worker":
                    phase = tc.arguments.get("phase", "")
                    self.event_bus.emit(
                        EventType.PHASE_STARTED,
                        phase=phase,
                        message=f"Coordinator spawning worker for {phase}",
                    )

    # ── Tool implementations ──

    def _spawn_worker(self, phase: str, instructions: str) -> ToolResult:
        """Spawn a WorkerAgent for the given phase."""
        if phase not in PHASES:
            return ToolResult(
                call_id="",
                output=None,
                success=False,
                error=f"Unknown phase: {phase}. Valid phases: {PHASES}",
            )

        # Create task in the task manager
        task = self.task_manager.create(
            TaskType.LOCAL_AGENT,
            parent_id="coordinator",
            description=f"Worker for phase: {phase}",
        )
        self.task_manager.transition(task.task_id, TaskStatus.RUNNING)

        if self.event_bus:
            self.event_bus.emit(
                EventType.PHASE_STARTED,
                phase=phase,
                message=f"Starting {phase}",
            )

        # Actually execute the worker
        try:
            from ppt_agent.coordinator.worker_agent import WorkerAgent, _SKILL_AUTONOMOUS_PHASES

            # Load skill for this phase (progressive disclosure)
            skill_content = None
            skill_name = _PHASE_SKILL.get(phase)
            if skill_name and self.skill_loader:
                skill_content = self.skill_loader.load_skill(skill_name)

            # For skill-autonomous phases, also try loading the actual
            # CatPaw skill (e.g., gptimage2-generator) if the phase-mapped
            # skill is a project-internal one
            if phase in _SKILL_AUTONOMOUS_PHASES and not skill_content:
                # Try the gptimage2-generator skill directly
                skill_content = self.skill_loader.load_skill("gptimage2-generator")

            worker = WorkerAgent(
                workspace=self.workspace,
                phase=phase,
                llm_client=self.llm_client,
                force=self.force,
                skill_content=skill_content,
                instructions=instructions,
            )

            # Build phase-appropriate task prompt and context
            if phase == "verification":
                # §9.2.3 Fresh Eyes: verification worker gets NO prior phase
                # context so it evaluates only the final artifacts.
                task_prompt = (
                    f"Execute the {phase} phase of the PPT generation pipeline. "
                    "You are reviewing with FRESH EYES. You have NO prior context "
                    "from implementation phases. Evaluate only the final artifacts."
                )
                phase_context: dict = {}
            elif phase in _SKILL_AUTONOMOUS_PHASES:
                task_prompt = (
                    f"Execute the {phase} phase of the PPT generation pipeline. "
                    f"You have the Skill documentation in your system prompt. "
                    f"Read it, understand the available commands, and autonomously "
                    f"execute them to generate the required outputs. "
                    f"Check the skill_context in your context for file paths "
                    f"and configuration details."
                )
                phase_context = self._build_phase_context(phase)
            else:
                task_prompt = f"Execute the {phase} phase of the PPT generation pipeline."
                phase_context = self._build_phase_context(phase)

            worker_result = worker.run(
                task_prompt=task_prompt,
                context=phase_context,
            )

            # Record results
            output = worker_result.final_output
            self._worker_results[phase] = {
                "status": worker_result.stop_reason.value,
                "turns": len(worker_result.turns),
                "output": str(output) if output else None,
                "error": worker_result.error,
            }

            self.task_manager.transition(
                task.task_id,
                TaskStatus.COMPLETED if worker_result.stop_reason == StopReason.COMPLETED else TaskStatus.FAILED,
                result=self._worker_results[phase],
            )

            if worker_result.stop_reason == StopReason.COMPLETED:
                self._completed_phases.append(phase)

                if self.event_bus:
                    self.event_bus.emit(
                        EventType.PHASE_COMPLETED,
                        phase=phase,
                        message=f"Completed {phase}",
                        artifact_path=str(output) if isinstance(output, Path) else None,
                    )

            return ToolResult(
                call_id="",
                output={
                    "task_id": task.task_id,
                    "phase": phase,
                    "status": worker_result.stop_reason.value,
                    "output": str(output) if output else None,
                    "turns_used": len(worker_result.turns),
                    "error": worker_result.error,
                },
                success=worker_result.stop_reason == StopReason.COMPLETED,
                error=worker_result.error,
            )

        except Exception as e:
            logger.error("Failed to spawn worker for %s: %s", phase, e)
            self.task_manager.transition(task.task_id, TaskStatus.FAILED, error=str(e))
            return ToolResult(
                call_id="",
                output=None,
                success=False,
                error=f"Worker spawn failed: {e}",
            )

    def _stop_worker(self, task_id: str) -> ToolResult:
        """Stop a running worker."""
        try:
            task = self.task_manager.get(task_id)
            if task.status == TaskStatus.RUNNING:
                self.task_manager.transition(task_id, TaskStatus.CANCELLED)
            return ToolResult(call_id="", output={"stopped": task_id}, success=True)
        except KeyError:
            return ToolResult(call_id="", output=None, success=False, error=f"Unknown task: {task_id}")

    def _send_message(self, recipient: str, message: str) -> ToolResult:
        """Send a message to a worker via mailbox."""
        worker_mailbox = Mailbox(self.workspace.root / ".mailbox" / f"{recipient}.jsonl")
        msg = worker_mailbox.append(
            sender="coordinator",
            recipient=recipient,
            message=message,
            priority=MessagePriority.TEAM_LEAD,
        )
        return ToolResult(call_id="", output=msg, success=True)

    def _synthesize_output(self, summary: str, warnings: list) -> ToolResult:
        """Produce the final synthesized output."""
        output = {
            "completed_phases": self._completed_phases,
            "total_phases": len(self.phases),
            "summary": summary,
            "warnings": warnings if isinstance(warnings, list) else [str(warnings)],
            "worker_results": self._worker_results,
        }
        return ToolResult(call_id="", output=output, success=True)

    def _build_phase_context(self, phase: str) -> dict:
        """Build context for a worker based on what artifacts already exist."""
        context: dict[str, Any] = {
            "phase": phase,
            "completed_phases": self._completed_phases,
            "force": self.force,
        }

        # Include relevant prior artifacts
        from ppt_agent.coordinator.phase_state import load_artifact
        artifact_map = {
            "outline_generation": ["source_summary"],
            "template_matching": ["outline"],
            "design_planning": ["outline", "template_meta"],
            "content_mapping": ["outline", "selected_template", "slide_design_plan", "source_summary"],
            "visual_generation": ["slide_contents"],
            "ppt_assembly": ["slide_contents"],
            "verification": ["slide_contents", "image_generation_report"],
        }

        for artifact_name in artifact_map.get(phase, []):
            try:
                context[artifact_name] = load_artifact(self.workspace, artifact_name)
            except (FileNotFoundError, Exception):
                pass

        return context
