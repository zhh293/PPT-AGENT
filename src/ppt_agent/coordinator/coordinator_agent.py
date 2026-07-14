"""CoordinatorAgent — the pure orchestration agent.

Phase 6 upgrade:
    - Removed PHASES hardcoded list; uses AgentCapability registry instead.
    - Added ThreadPoolExecutor for async parallel worker execution.
    - 6 orchestration tools: spawn_agent, check_artifacts, wait_agents,
      review_result, request_user_review, synthesize_output.
    - Autonomous LLM-driven decision making with dependency rules.
"""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path
from typing import Any

from ppt_agent.context.memory_layers import build_memory_context
from ppt_agent.coordinator.capabilities import CAPABILITIES_IN_ORDER, AgentCapability, get_capability
from ppt_agent.coordinator.event_bus import EventBus, EventType
from ppt_agent.coordinator.tool_factory import ToolFactory
from ppt_agent.llm.json_repair import repair_json
from ppt_agent.llm.messages import LLMResult
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.runtime.agent_loop import (
    AgentLoop, AgentLoopResult, StopReason, ToolCall, ToolResult,
)
from ppt_agent.runtime.agent_context import AgentContext, TeammateContext, agent_scope, teammate_scope
from ppt_agent.runtime.mailbox import Mailbox, MessagePriority
from ppt_agent.runtime.task_manager import TaskManager
from ppt_agent.runtime.task_types import TaskStatus, TaskType
from ppt_agent.skills.loader import SkillLoader

logger = logging.getLogger(__name__)

# Legacy alias — kept for backward compatibility with workflow.py
PHASES = [c.capability_id for c in CAPABILITIES_IN_ORDER]


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
            job_root=workspace.root,
        )
        self.workspace = workspace
        self.task_manager = task_manager or TaskManager()
        self.event_bus = event_bus
        self.skill_loader = skill_loader or SkillLoader(root=Path(".catpaw/skills"))
        self._phase_constraint = phases  # optional scope constraint from CLI
        self.force = force

        # Phase 6: async execution + thread safety
        self._executor = ThreadPoolExecutor(max_workers=3)
        self._futures: dict[str, Future] = {}
        self._state_lock = threading.Lock()

        # Mailbox
        self.mailbox = Mailbox(workspace.root / ".mailbox" / "coordinator.jsonl")

        # State
        self._worker_results: dict[str, Any] = {}
        self._completed: list[str] = []

    @property
    def completed_phases(self) -> list[str]:
        """Capability IDs that have completed (read-only snapshot)."""
        with self._state_lock:
            return list(self._completed)

    @property
    def worker_results(self) -> dict[str, Any]:
        """Per-phase worker results (read-only snapshot)."""
        with self._state_lock:
            return dict(self._worker_results)

    def build_system_prompt(self) -> str:
        memory_context = build_memory_context(self.workspace.root)
        skill_catalog = self.skill_loader.get_catalog_summary() if self.skill_loader else ""

        capabilities_desc = "\n".join(
            f"- **{c.capability_id}**: {c.description}" for c in CAPABILITIES_IN_ORDER
        )
        constraint = ""
        if self._phase_constraint:
            constraint = f"\nScope constraint: only use these capabilities: {self._phase_constraint}"

        return f"""You are the Coordinator for a PPT generation system. You are a PURE ORCHESTRATOR.

## Available Capabilities
{capabilities_desc}

## Dependency Rules
- document_analysis must complete before outline_generation
- outline_generation must complete before template_matching and design_planning
- content_mapping depends on outline + template + design_plan + source_summary + template_zones
- visual_generation depends on slide_contents
- ppt_assembly depends on slide_contents (and optionally visual_generation)
- verification depends on ppt_assembly

## Parallel Opportunities
- outline_generation and template_matching can run in parallel after document_analysis
- design_planning can run with template_matching if outline is ready

## Your Job
Decide AUTONOMOUSLY which capabilities to invoke and in what order.
Use spawn_agent(capability=..., async=true) for parallel work.
Use check_artifacts to verify outputs. Use wait_agents after async spawns.
After content_mapping, use request_user_review for the user to approve slide_contents.
When all work is done, use synthesize_output.{constraint}

## Completed
{json.dumps(self._completed)}

## Skills
{skill_catalog}

{f'## Memory{chr(10)}{memory_context}' if memory_context else ''}"""

    def get_available_tools(self) -> list[dict]:
        """Phase 6: 6 orchestration tools with JSON Schema parameters."""
        return [
            {"name": "spawn_agent", "description": "Spawn a worker agent for a capability. Set async=true to run in background.",
             "parameters": {
                 "type": "object",
                 "properties": {
                     "capability": {"type": "string", "description": "Capability ID to execute (e.g. document_analysis)"},
                     "instructions": {"type": "string", "description": "Optional extra instructions for the worker"},
                     "async": {"type": "boolean", "description": "Run in background without waiting (default: false)"},
                 },
                 "required": ["capability"],
             }},
            {"name": "check_artifacts", "description": "Check which artifact files exist in the workspace.",
             "parameters": {
                 "type": "object",
                 "properties": {
                     "artifact_names": {"type": "array", "items": {"type": "string"}, "description": "List of artifact names to check (optional, checks all if omitted)"},
                 },
             }},
            {"name": "wait_agents", "description": "Wait for async agent tasks to complete.",
             "parameters": {
                 "type": "object",
                 "properties": {
                     "task_ids": {"type": "array", "items": {"type": "string"}, "description": "Task IDs returned by spawn_agent(async=true)"},
                     "timeout": {"type": "integer", "description": "Max seconds to wait (default: 300)"},
                 },
                 "required": ["task_ids"],
             }},
            {"name": "review_result", "description": "Review a completed worker agent's output and status.",
             "parameters": {
                 "type": "object",
                 "properties": {
                     "task_id": {"type": "string", "description": "Task ID of the worker to review"},
                 },
                 "required": ["task_id"],
             }},
            {"name": "request_user_review", "description": "Pause the pipeline and request user review of slide_contents.",
             "parameters": {
                 "type": "object",
                 "properties": {
                     "message": {"type": "string", "description": "Message to display to the user explaining what needs review"},
                 },
                 "required": ["message"],
             }},
            {"name": "synthesize_output", "description": "Produce the final output after all pipeline phases are done.",
             "parameters": {
                 "type": "object",
                 "properties": {
                     "summary": {"type": "string", "description": "Summary of what was accomplished"},
                     "warnings": {"type": "array", "items": {"type": "string"}, "description": "Any warnings or issues encountered"},
                 },
             }},
        ]

    def execute_tool(self, tool_call: ToolCall) -> ToolResult:
        name = tool_call.tool_name
        args = tool_call.arguments

        if name == "spawn_agent":
            return self._spawn_agent(args.get("capability", args.get("phase", "")),
                                      args.get("instructions", ""),
                                      str(args.get("async", "false")).lower() == "true")
        elif name == "check_artifacts":
            return self._check_artifacts(args.get("artifact_names", []))
        elif name == "wait_agents":
            return self._wait_agents(args.get("task_ids", []), int(args.get("timeout", 300)))
        elif name == "review_result":
            return self._review_result(args.get("task_id", ""))
        elif name == "request_user_review":
            return self._request_user_review(args.get("message", ""))
        elif name == "synthesize_output":
            return self._synthesize_output(args.get("summary", ""), args.get("warnings", []))
        # Legacy tool names
        elif name == "spawn_worker":
            # Legacy alias: accepts "phase" (old name) or "capability" (current name)
            return self._spawn_agent(
                args.get("capability", args.get("phase", "")),
                args.get("instructions", ""),
                False,
            )
        elif name == "stop_worker":
            return ToolResult(call_id=tool_call.call_id, output={"stopped": args.get("task_id", "")}, success=True)
        elif name == "send_message":
            return self._send_message(args.get("recipient", ""), args.get("message", ""))
        return ToolResult(call_id=tool_call.call_id, output=None, success=False, error=f"Unknown tool: {name}")

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
                if tc.tool_name in ("spawn_agent", "spawn_worker"):
                    phase = tc.arguments.get("capability", tc.arguments.get("phase", ""))
                    self.event_bus.emit(
                        EventType.PHASE_STARTED,
                        phase=phase,
                        message=f"Coordinator spawning worker for {phase}",
                    )

    # ── Tool implementations ──

    def _spawn_agent(self, capability_id: str, instructions: str, is_async: bool) -> ToolResult:
        """Spawn a WorkerAgent for the given capability (Phase 6)."""
        if not capability_id:
            return ToolResult(call_id="", output=None, success=False, error="capability is required")
        try:
            capability = get_capability(capability_id)
        except KeyError:
            return ToolResult(call_id="", output=None, success=False,
                              error=f"Unknown capability: {capability_id}. Available: {list(CAPABILITIES_IN_ORDER)}")

        task = self.task_manager.create(TaskType.LOCAL_AGENT, parent_id="coordinator",
                                         description=f"Worker: {capability_id}")
        self.task_manager.transition(task.task_id, TaskStatus.RUNNING)

        if self.event_bus:
            self.event_bus.emit(EventType.PHASE_STARTED, phase=capability_id, message=f"Starting {capability_id}")
            self.event_bus.emit(EventType.AGENT_SPAWN, phase=capability_id, message=f"Spawning worker for {capability_id}")

        if is_async:
            future = self._executor.submit(self._run_worker, capability, task, instructions)
            self._futures[task.task_id] = future
            return ToolResult(call_id="", output={"task_id": task.task_id, "capability": capability_id, "status": "running_async"}, success=True)

        return self._run_worker(capability, task, instructions)

    def _run_worker(self, capability: AgentCapability, task, instructions: str) -> ToolResult:
        """Execute a worker synchronously and return the result."""
        capability_id = capability.capability_id
        try:
            from ppt_agent.coordinator.worker_agent import WorkerAgent

            skill_content = None
            if capability.skill_name and self.skill_loader:
                skill_content = self.skill_loader.load_skill(capability.skill_name)
                if not skill_content and capability.skill_name == "gptimage2-generator":
                    skill_content = self.skill_loader.load_skill("gptimage2-generator")

            # Build ToolRegistry for this capability
            factory = ToolFactory(self.workspace)
            registry = factory.create_registry_for_capability(capability, skill_loader=self.skill_loader, llm_client=self.llm_client)

            worker = WorkerAgent(
                workspace=self.workspace,
                phase_or_capability=capability,
                llm_client=self.llm_client,
                force=self.force,
                skill_content=skill_content,
                instructions=instructions,
                registry=registry,
            )

            task_prompt = f"Execute the {capability_id} capability: {capability.description}"
            phase_context = self._build_phase_context(capability_id)

            with teammate_scope(TeammateContext(workspace_root=self.workspace.root, event_bus=self.event_bus,
                                                 skill_loader=self.skill_loader, job_id=self.workspace.root.name, team_role="worker")):
                with agent_scope(AgentContext(agent_id=f"worker-{capability_id}", phase=capability_id)):
                    worker_result = worker.run(task_prompt=task_prompt, context=phase_context)

            output = worker_result.final_output
            with self._state_lock:
                self._worker_results[capability_id] = {"status": worker_result.stop_reason.value, "turns": len(worker_result.turns),
                                                        "output": str(output) if output else None, "error": worker_result.error}
            self.task_manager.transition(task.task_id, TaskStatus.COMPLETED if worker_result.stop_reason == StopReason.COMPLETED else TaskStatus.FAILED,
                                          result=self._worker_results[capability_id])

            if worker_result.stop_reason == StopReason.COMPLETED:
                with self._state_lock:
                    self._completed.append(capability_id)
                if self.event_bus:
                    self.event_bus.emit(EventType.PHASE_COMPLETED, phase=capability_id, message=f"Completed {capability_id}")
                    self.event_bus.emit(EventType.AGENT_COMPLETE, phase=capability_id, message=f"Worker {capability_id} completed")

            return ToolResult(call_id="", output={"task_id": task.task_id, "capability": capability_id,
                               "status": worker_result.stop_reason.value, "turns_used": len(worker_result.turns),
                               "error": worker_result.error}, success=worker_result.stop_reason == StopReason.COMPLETED,
                               error=worker_result.error)
        except Exception as e:
            logger.error("Worker %s failed: %s", capability_id, e)
            self.task_manager.transition(task.task_id, TaskStatus.FAILED, error=str(e))
            return ToolResult(call_id="", output=None, success=False, error=str(e))

    def _check_artifacts(self, artifact_names: list) -> ToolResult:
        result = {}
        names: list[str] = []
        if artifact_names:
            names = [str(n) for n in artifact_names]
        else:
            # List all JSON files in workspace
            try:
                for p in self.workspace.root.glob("*.json"):
                    names.append(p.stem)
            except Exception:
                pass
        for n in names:
            p = self.workspace.artifact_path(n)
            result[n] = {"exists": p.exists(), "size": p.stat().st_size if p.exists() else 0}
        return ToolResult(call_id="", output=result, success=True)

    def _wait_agents(self, task_ids, timeout: int) -> ToolResult:
        import time
        if isinstance(task_ids, str):
            task_ids = [task_ids]
        results = {}
        deadline = time.monotonic() + timeout
        for tid in task_ids:
            remaining = max(0, deadline - time.monotonic())
            future = self._futures.pop(tid, None)
            if future:
                try:
                    r = future.result(timeout=remaining)
                    results[tid] = {"status": "completed", "result": str(r.output) if r.output else None}
                except Exception as e:
                    results[tid] = {"status": "failed", "error": str(e)}
            else:
                results[tid] = {"status": "unknown", "error": "task_id not found"}

        # Clean up any remaining futures on each wait call — prevents unbounded
        # growth if someone forgets to wait on an async task.
        done_tids = [tid for tid, f in list(self._futures.items()) if f.done()]
        for tid in done_tids:
            self._futures.pop(tid, None)

        return ToolResult(call_id="", output=results, success=True)

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the thread pool executor."""
        self._executor.shutdown(wait=wait, cancel_futures=not wait)

    def _review_result(self, task_id: str) -> ToolResult:
        wr = self._worker_results.get(task_id, {})
        return ToolResult(call_id="", output=wr, success=bool(wr))

    def _request_user_review(self, message: str) -> ToolResult:
        review_path = self.workspace.root / "review_pending.json"
        review_path.write_text(json.dumps({"message": message, "status": "pending", "timestamp": __import__("time").time()}, ensure_ascii=False))
        return ToolResult(call_id="", output={"review_file": str(review_path), "message": message}, success=True)

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
        with self._state_lock:
            output = {
                "completed_phases": list(self._completed),
                "total_phases": len(self._completed),
                "summary": summary,
                "warnings": warnings if isinstance(warnings, list) else [str(warnings)],
                "worker_results": dict(self._worker_results),
            }
        return ToolResult(call_id="", output=output, success=True)

    def _build_phase_context(self, phase: str) -> dict:
        with self._state_lock:
            completed = list(self._completed)
        context: dict[str, Any] = {"phase": phase, "completed_phases": completed, "force": self.force}

        # Include relevant prior artifacts
        from ppt_agent.coordinator.phase_state import load_artifact
        artifact_map = {
            "outline_generation": ["source_summary"],
            "template_matching": ["outline"],
            "design_planning": ["outline", "template_meta"],
            "content_mapping": ["outline", "selected_template", "slide_design_plan", "source_summary"],
            "visual_generation": ["slide_contents"],
            "ppt_assembly": ["slide_contents", "template_zones"],
            "verification": ["slide_contents", "image_generation_report"],
        }

        for artifact_name in artifact_map.get(phase, []):
            try:
                context[artifact_name] = load_artifact(self.workspace, artifact_name)
            except (FileNotFoundError, Exception):
                pass

        return context
