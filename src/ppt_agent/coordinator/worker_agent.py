"""WorkerAgent — independent agent loop for PPT pipeline capabilities.

Phase 6 upgrade:
    - Receives AgentCapability instead of phase string.
    - Uses ToolFactory + ToolRegistry for tool execution (RBAC enforced).
    - Removed compose_artifact — uses write_artifact tool instead.
    - Fallback delegates to capability.fallback_module.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

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

logger = logging.getLogger(__name__)

# Map phase names to their worker modules (used for deterministic fallback)
_PHASE_WORKERS = {
    "document_analysis": "ppt_agent.workers.document_analyst",
    "outline_generation": "ppt_agent.workers.outline_generator",
    "template_matching": "ppt_agent.workers.template_matcher",
    "design_planning": "ppt_agent.workers.design_director",
    "content_mapping": "ppt_agent.workers.content_mapper",
    "visual_generation": "ppt_agent.workers.image_generator",
    "ppt_assembly": "ppt_agent.workers.ppt_assembler",
    "verification": "ppt_agent.workers.ppt_verifier",
}

# Phases that accept llm_client in their deterministic worker
_LLM_PHASES = {
    "document_analysis",
    "outline_generation",
    "design_planning",
    "content_mapping",
    "verification",
}

# Phase categories — determines which tools the agent gets
_CONTENT_REASONING_PHASES = {
    "document_analysis",
    "outline_generation",
    "design_planning",
    "content_mapping",
    "verification",
}

_OPERATION_PHASES = {
    "template_matching",
    "ppt_assembly",
}

# ── ALL phases run through agent loop. ──
# Each phase's tools delegate to the worker function internally,
# so the LLM drives execution but the core logic is our code.
# No phases are skipped — the agent decides when to call each tool.
_WORKER_DIRECT_PHASES: set[str] = set()  # Empty — agent loop for all phases

_SKILL_AUTONOMOUS_PHASES = {
    "visual_generation",
}

# ── Phase 4.4: Sandbox security — blocked shell patterns ────────────
# IMPORTANT: This string-match blacklist is a BEST-EFFORT defence-in-depth
# layer.  It is NOT a security boundary — shell=True with user-supplied
# arguments is fundamentally unsafe.  Use SafeFilesystem for path
# containment and consider OS-level sandboxing (containers, seccomp) for
# production deployments.
#
# Keep in sync with tool_impls/visual_generation.py:run_shell_command.
_BLOCKED_PATTERNS = (
    # Block only root-targeting rm variants (not /tmp or workspace paths)
    "rm -rf / ",
    "rm -rf /* ",
    "rm -r -f / ",
    "rm -r -f /* ",
    # Block filesystem creation commands
    "mkfs",
    "mke2fs",
    # Block raw device writes
    "dd if=",
    # Block fork bombs
    ":(){ :|:& };:",
)

# Allowlist of legitimate worker tool names (AGENT_ARCHITECTURE.md §5.1, §5.3)
_WORKER_ALLOWLIST: set[str] = {
    "read_input_files",
    "read_artifact",
    "compose_artifact",
    "write_artifact",
    "validate_output",
    "search_templates",
    "assemble_pptx",
    "check_file",
    "run_shell_command",
    "check_file_exists",
    "list_directory",
    "execute_phase",
}

# What each phase reads (input artifacts) and writes (output artifact)
_PHASE_IO = {
    "document_analysis": {
        "inputs": [],  # reads raw files from input_dir
        "output": "source_summary",
        "reads_input_dir": True,
    },
    "outline_generation": {
        "inputs": ["source_summary"],
        "output": "outline",
    },
    "template_matching": {
        "inputs": ["outline"],
        "output": "selected_template",
        "extra_outputs": ["template_meta", "template_zones"],
    },
    "design_planning": {
        "inputs": ["outline", "template_meta"],
        "output": "slide_design_plan",
    },
    "content_mapping": {
        "inputs": ["outline", "selected_template", "slide_design_plan", "source_summary", "template_zones"],
        "output": "slide_contents",
    },
    "visual_generation": {
        "inputs": ["slide_contents", "template_zones"],
        "output": "image_generation_report",
    },
    "ppt_assembly": {
        "inputs": ["slide_contents", "template_zones"],
        "output": None,  # produces final.pptx
    },
    "verification": {
        "inputs": ["slide_contents", "image_generation_report"],
        "output": "validation_report",
    },
}

# Prompt templates for each phase — what to load
_PROMPT_DIRS = Path(__file__).resolve().parent.parent / "llm" / "prompts"

# Phase-specific system prompt descriptions
_PHASE_DESCRIPTIONS = {
    "document_analysis": (
        "You are a Document Analyst. Your job is to read the uploaded project "
        "materials (text files, PDFs, images) and produce a structured JSON summary "
        "(source_summary) that captures: project name, domain, audience, tone, "
        "value proposition, capabilities, evidence items, and image inventory."
    ),
    "outline_generation": (
        "You are a PPT Outline Architect. Your job is to read the source_summary "
        "and create a structured presentation outline (outline) with slides, "
        "each having: type, title, purpose, bullets, source_refs, image_needs."
    ),
    "template_matching": (
        "You are a Template Selector. Your job is to read the outline, "
        "search the template index for matching templates, evaluate the top "
        "candidates, and produce a selected_template and template_meta artifact. "
        "IMPORTANT: In your compose_artifact output, use the key 'template_id' "
        "(NOT 'selected_template_id') for the chosen template identifier. "
        "Also include: selection_status, template_path, ranking, rationale."
    ),
    "design_planning": (
        "You are a Design Director. Your job is to read the outline and "
        "template_meta, then create a detailed slide_design_plan with "
        "theme_profile, per-slide layout decisions, visual density, "
        "and block plans for each slide."
    ),
    "content_mapping": (
        "You are a Content Editor. Your job is to read the outline, design plan, "
        "template selection, and source summary, then produce slide_contents — "
        "the final zone-level content mapping for every slide, with exact text, "
        "positions, image references, and fit status."
    ),
    "ppt_assembly": (
        "You are a PPT Assembler. Your job is to place content into template "
        "shapes by reading template_zones.json and slide_contents.json, then "
        "deciding exactly which zone_id gets which content. "
        "You MUST use zone_ids from template_zones — NEVER invent them."
    ),
    "verification": (
        "You are a Quality Reviewer with fresh eyes. You have NOT seen any "
        "prior conversation — you only see the final artifacts. Evaluate "
        "content completeness, visual consistency, and text accuracy."
    ),
}


class WorkerAgent(AgentLoop):
    """Independent agent loop for a single pipeline phase.

    Each phase gets a tool set tailored to its domain:

    - Content reasoning phases: read_input_files, read_artifact, analyze_content,
      compose_artifact, write_artifact, validate_output
    - Operation phases: read_artifact, search_templates / assemble_pptx,
      write_artifact, check_file, validate_output
    - Skill-autonomous phases: run_shell_command, check_file_exists,
      list_directory, read_artifact, write_artifact, validate_output

    The LLM uses these tools in whatever order makes sense — it's the
    decision maker, not just a button presser.

    Falls back to deterministic worker if LLM can't complete the task.
    """

    def __init__(
        self,
        workspace: JobWorkspace,
        phase_or_capability,
        llm_client,
        *,
        force: bool = False,
        skill_content: str | None = None,
        instructions: str = "",
        max_turns: int | None = None,
        registry=None,
    ) -> None:
        # Support both AgentCapability and legacy string phase
        from ppt_agent.coordinator.capabilities import AgentCapability, get_capability
        if isinstance(phase_or_capability, str):
            try:
                self.capability = get_capability(phase_or_capability)
            except KeyError:
                self.capability = AgentCapability(capability_id=phase_or_capability, description=phase_or_capability,
                                                   input_artifacts=[], output_artifacts=[], tools=[], max_turns=10)
        else:
            self.capability = phase_or_capability

        max_turns = max_turns or self.capability.max_turns
        self.phase = self.capability.capability_id  # backward compat
        super().__init__(
            agent_id=f"worker-{self.phase}",
            llm_client=llm_client,
            max_turns=max_turns,
            max_consecutive_errors=3,
            job_root=workspace.root,
        )
        self.workspace = workspace
        self.force = force
        self.skill_content = skill_content
        self.instructions = instructions
        self._registry = registry

    @property
    def _phase_category(self) -> str:
        if self.phase in _SKILL_AUTONOMOUS_PHASES:
            return "skill_autonomous"
        if self.phase in _OPERATION_PHASES:
            return "operation"
        return "content_reasoning"

    def run(self, task_prompt: str, context: dict | None = None) -> AgentLoopResult:
        """Execute the worker — LLM loop with deterministic fallback.

        For phases in _WORKER_DIRECT_PHASES, skips the agent loop entirely
        and delegates straight to our hand-crafted worker code.
        """
        # ── Worker-direct phases: skip agent loop, use our code ──
        if self.phase in _WORKER_DIRECT_PHASES:
            logger.info("Phase %s: worker-direct — calling %s", self.phase, self.capability.fallback_module)
            output = self._run_deterministic_worker()
            agent_result = AgentLoopResult(
                stop_reason=StopReason.COMPLETED,
                final_output=str(output) if output else None,
            )
            if self.phase == "visual_generation":
                self._post_skill_execution()
            return agent_result

        # Enrich context for skill-autonomous phases
        if self.phase in _SKILL_AUTONOMOUS_PHASES:
            context = self._enrich_skill_context(context or {})

        # Try the agent loop
        result = super().run(task_prompt, context)

        if result.stop_reason == StopReason.COMPLETED:
            if self.phase in _SKILL_AUTONOMOUS_PHASES:
                self._post_skill_execution()
            return result

        # Fallback to deterministic
        logger.info(
            "Worker %s agent loop ended with %s, falling back to deterministic",
            self.phase, result.stop_reason.value,
        )
        return self._fallback_execution(result)

    # ── Context enrichment ──

    def _enrich_skill_context(self, context: dict) -> dict:
        if self.phase == "visual_generation":
            from ppt_agent.workers.image_generator import (
                prepare_generation_config,
                build_skill_context,
            )
            try:
                _config_path, config = prepare_generation_config(self.workspace, mode="all")
                skill_ctx = build_skill_context(self.workspace, config)
                # Point output to background_images/ (the new directory)
                skill_ctx["output_dir"] = str(self.workspace.background_images_dir)
                context["skill_context"] = skill_ctx
            except Exception as e:
                logger.warning("Failed to prepare skill context: %s", e)
        return context

    def _post_skill_execution(self) -> None:
        if self.phase == "visual_generation":
            import time
            from ppt_agent.coordinator.phase_state import load_artifact, write_artifact
            try:
                config = load_artifact(self.workspace, "image_generation_config")

                # ── Wait for gptimage2 to ACTUALLY finish ──
                # batch_report.json is written progressively (one entry per image).
                # We must wait until done == total, not just until the file exists.
                batch_report_path = self.workspace.background_images_dir / "batch_report.json"
                deadline = time.monotonic() + 600  # 10 min max for all images
                last_progress = ""
                while time.monotonic() < deadline:
                    if batch_report_path.exists():
                        import json
                        try:
                            batch = json.loads(batch_report_path.read_text(encoding="utf-8"))
                            done = batch.get("done", 0)
                            total = batch.get("total", 0)
                            progress = batch.get("progress", "")
                            if progress != last_progress:
                                logger.info("Image generation progress: %s", progress)
                                last_progress = progress
                            if total > 0 and done >= total:
                                break  # All done!
                        except (json.JSONDecodeError, OSError):
                            pass  # File may be mid-write — retry
                    time.sleep(10)
                else:
                    logger.warning("Timed out waiting for image generation after 10 min (last: %s)", last_progress)

                # Build report from batch_report.json or fall back to file check
                if batch_report_path.exists():
                    import json
                    batch = json.loads(batch_report_path.read_text(encoding="utf-8"))
                    details = batch.get("details", {})
                    success_list = details.get("success", [])
                    failed_list = details.get("failed", [])
                    success_indices = {s["index"] for s in success_list}
                    failed_indices  = {s["index"] for s in failed_list}

                    slides = config.get("slides", [])
                    slide_results = []
                    generated = 0
                    fallback = 0
                    for slide_entry in slides:
                        idx = slide_entry["index"]
                        if idx in success_indices:
                            slide_results.append({"slide_index": idx, "status": "generated",
                                                  "prompt_used": slide_entry.get("prompt", "")})
                            generated += 1
                        elif idx in failed_indices:
                            slide_results.append({"slide_index": idx, "status": "failed",
                                                  "reason": "API error or credit exhausted",
                                                  "prompt_used": slide_entry.get("prompt", "")})
                            fallback += 1
                        else:
                            slide_results.append({"slide_index": idx, "status": "fallback_used",
                                                  "reason": "Image generation did not complete in time",
                                                  "prompt_used": slide_entry.get("prompt", "")})
                            fallback += 1

                    status = "all_fallback" if generated == 0 else ("partial" if fallback > 0 else "all_generated")
                    report = {
                        "job_id": self.workspace.root.name,
                        "status": status,
                        "total_slides": len(slides),
                        "generated": generated,
                        "fallback": fallback,
                        "slides": slide_results,
                        "warnings": [f"{fallback} slide(s) have no background image – will use solid color."] if fallback > 0 else [],
                        "execution": {"method": "agent_autonomous_skill", "batch_progress": batch.get("progress", "?")},
                    }
                else:
                    from ppt_agent.workers.image_generator import check_generated_images
                    report = check_generated_images(self.workspace, config)
                    report["execution"] = {"method": "agent_autonomous_skill", "note": "No batch_report.json — checked files directly"}

                write_artifact(self.workspace, "image_generation_report", report)
                logger.info("Visual generation report: generated=%d fallback=%d status=%s",
                           report.get("generated", 0), report.get("fallback", 0), report.get("status", "?"))
            except Exception as e:
                logger.warning("Post-execution check failed: %s", e)

    # ── Fallback ──

    def _fallback_execution(self, agent_result: AgentLoopResult) -> AgentLoopResult:
        """Fall back to deterministic worker via capability.fallback_module (Phase 6)."""
        try:
            output = self._run_deterministic_worker()
            agent_result.stop_reason = StopReason.COMPLETED
            agent_result.final_output = output
            agent_result.error = None
            return agent_result
        except Exception as e:
            logger.error("Fallback for %s failed: %s", self.phase, e)
            agent_result.stop_reason = StopReason.ERROR_BUDGET
            agent_result.error = f"Both agent loop and fallback failed: {e}"
            return agent_result

    def _run_deterministic_worker(self) -> Any:
        import importlib
        # Phase 6: use capability.fallback_module or fall back to legacy _PHASE_WORKERS
        module_path = self.capability.fallback_module or _PHASE_WORKERS.get(self.phase)
        if not module_path:
            raise ValueError(f"No fallback module for: {self.phase}")
        module = importlib.import_module(module_path)
        if self.phase in _LLM_PHASES and self.llm_client is not None:
            return module.run(self.workspace, force=self.force, llm_client=self.llm_client)
        return module.run(self.workspace, force=self.force)

    # ── System prompt ──

    def _build_memory_section(self) -> str:
        """Read the three-layer memory files and return a formatted section."""
        try:
            from ppt_agent.context.memory_layers import build_memory_context
            ctx = build_memory_context(self.workspace.root)
            if ctx:
                return f"\n## Memory (Cross-Session Context)\n\n{ctx}\n"
        except Exception:
            pass
        return ""

    def build_system_prompt(self) -> str:
        cat = self._phase_category
        if cat == "skill_autonomous":
            return self._build_skill_autonomous_prompt()
        if cat == "operation":
            return self._build_operation_prompt()
        return self._build_content_reasoning_prompt()

    def _build_content_reasoning_prompt(self) -> str:
        phase_io = _PHASE_IO.get(self.phase, {})
        inputs = phase_io.get("inputs", [])
        output = phase_io.get("output", "unknown")
        reads_input_dir = phase_io.get("reads_input_dir", False)

        desc = _PHASE_DESCRIPTIONS.get(self.phase, f"Execute the {self.phase} phase.")

        input_section = ""
        if reads_input_dir:
            input_section = (
                "\nYou have access to the raw input files via read_input_files. "
                "Start by reading the uploaded materials to understand the project."
            )
        if inputs:
            input_section += (
                f"\nYour input artifacts: {', '.join(inputs)}. "
                "Use read_artifact to load them and understand the prior work."
            )

        prompt_section = ""
        prompt_file = _PROMPT_DIRS / f"{self.phase.replace('_', '_')}.md"
        if prompt_file.exists():
            try:
                prompt_text = prompt_file.read_text(encoding="utf-8")
                prompt_section = f"\n\n## Phase-Specific Guide\n\n{prompt_text}"
            except Exception:
                pass

        skill_section = ""
        if self.skill_content:
            skill_section = f"\n\n## Loaded Skill\n\n{self.skill_content}"

        extra = ""
        if self.instructions:
            extra = f"\n\n## Additional Instructions\n\n{self.instructions}"

        memory_section = self._build_memory_section()

        return f"""You are a WorkerAgent executing the '{self.phase}' phase.

## Identity
{desc}

## Your Workflow
1. READ — Use read_artifact (and read_input_files if available) to load your inputs.
   Understand the data thoroughly before producing output.
2. REASON — Think about what the output should contain based on your inputs.
   Consider domain, audience, tone, and the requirements of downstream phases.
3. COMPOSE — Use compose_artifact to generate the output JSON. Provide detailed
   instructions about what the artifact should contain. The tool will produce
   a structured JSON following the phase's schema.
4. REVIEW — Check the composed artifact. Does it have all required fields?
   Is the content coherent and complete? Are source references preserved?
5. WRITE — Use write_artifact to save the final result.
6. VALIDATE — Use validate_output to confirm the artifact is well-formed.
7. DONE — Signal completion.

You are NOT a button-presser. You actively READ, REASON, and DECIDE what
goes into the output. If something looks wrong, fix it before writing.
{input_section}

## Output
Your output artifact is: {output}

## Workspace: {self.workspace.root}
## Force: {self.force}
{memory_section}{prompt_section}{skill_section}{extra}
"""

    def _build_operation_prompt(self) -> str:
        desc = _PHASE_DESCRIPTIONS.get(self.phase, f"Execute the {self.phase} phase.")
        phase_io = _PHASE_IO.get(self.phase, {})
        inputs = phase_io.get("inputs", [])

        operation_guide = ""
        if self.phase == "template_matching":
            operation_guide = """
## Your Workflow
1. READ the outline artifact to understand the presentation's domain, audience, and tone.
2. SEARCH templates using search_templates with query terms derived from the outline.
3. EVALUATE the ranked results — consider domain fit, layout compatibility, tone match.
4. COMPOSE and WRITE the selected_template artifact with your ranking.
5. COMPOSE and WRITE the template_meta artifact with layout details.
6. VALIDATE both outputs.
7. DONE.
"""
        elif self.phase == "ppt_assembly":
            operation_guide = """
## Your Role — Content Adaptation Specialist

You have ONE job: make sure the text FITS in its template zone.

The heavy lifting is already done:
- content_mapping assigned every piece of content to a zone_id
- The tool auto-builds mappings from slide_contents

Your job:
1. Call **assemble_pptx** (no arguments needed on first call)
2. Read the result — if it contains **overflow_zones**, those need fixing
3. For each overflow zone, shorten or merge the content so it fits:
   - Title/subtitle too long → shorten while keeping the key message
   - Too many bullets → merge related ones, drop the least important
   - Bullet too long → compress to the core point
4. Call **assemble_pptx** again with:
   ```json
   {"adaptations": {slide_index: {content_type: {content: ...}}}}
   ```
5. Check the result — repeat if overflow_zones still present

## CRITICAL RULES
- ONLY change **content** — zone_id is immutable and auto-assigned
- NEVER invent or change zone_id, formatting, or position
- adaptations format: `{slide_index: {"title"|"bullets": {content: ...}}}`
- Keep slide_index as an integer, content_type as a string
- If content fits, pass nothing — the tool handles the happy path

## Example
Tool returns overflow: slide 3 bullets zone has 5 items, capacity is 3 lines.
Your adaptation:
```json
{
  "adaptations": {
    "3": {
      "bullets": {
        "content": [
          "AI智能推荐引擎与实时处理",
          "用户画像个性化推送",
          "百万级并发分布式架构"
        ]
      }
    }
  }
}
```
"""



        skill_section = ""
        if self.skill_content:
            skill_section = f"\n\n## Loaded Skill\n\n{self.skill_content}"

        extra = ""
        if self.instructions:
            extra = f"\n\n## Additional Instructions\n\n{self.instructions}"

        memory_section = self._build_memory_section()

        return f"""You are a WorkerAgent executing the '{self.phase}' phase.

## Identity
{desc}

## Input artifacts: {', '.join(inputs) if inputs else 'none'}
{operation_guide}
## Workspace: {self.workspace.root}
## Force: {self.force}
{memory_section}{skill_section}{extra}
"""

    def _build_skill_autonomous_prompt(self) -> str:
        skill_doc = ""
        if self.skill_content:
            skill_doc = f"""

## Skill Documentation

Below is the FULL documentation for the Skill you should use. Read it carefully,
understand the available commands, API details, and workflows, then decide
how to accomplish the task.

---
{self.skill_content}
---
"""
        extra = ""
        if self.instructions:
            extra = f"\n\n## Additional Instructions\n\n{self.instructions}"

        memory_section = self._build_memory_section()

        return f"""You are a WorkerAgent executing the '{self.phase}' phase.

## Your Role
You are an AUTONOMOUS agent operating a Skill. You have shell access and file
tools. Your job is to:
1. Read and understand the Skill documentation provided below
2. Reason about which commands to run, in what order, with what parameters
3. Execute commands via the run_shell_command tool
4. Check results via check_file_exists and list_directory
5. Handle errors and edge cases (e.g., insufficient credits → switch account)
6. Write the final report artifact when done

## How You Work
You operate EXACTLY like CatPaw's host agent:
- You READ the Skill documentation (provided in your system prompt)
- You REASON about what to do (which script, what arguments)
- You DECIDE and EXECUTE commands through your tools
- You OBSERVE results and ADAPT (retry, switch accounts, etc.)

## Phase: {self.phase}
## Workspace: {self.workspace.root}
## Force: {self.force}
{memory_section}{skill_doc}{extra}
"""

    # ── Tool definitions ──

    def get_available_tools(self) -> list[dict]:
        # Phase 6: use ToolRegistry if available
        if self._registry is not None:
            return self._registry.get_tools_for_role_as_dicts("worker")
        # Legacy fallback
        cat = self._phase_category
        if cat == "skill_autonomous":
            return self._get_skill_autonomous_tools()
        if cat == "operation":
            return self._get_operation_tools()
        return self._get_content_reasoning_tools()

    def _get_content_reasoning_tools(self) -> list[dict]:
        """Tools for content reasoning phases — the LLM reads, reasons, composes."""
        tools = []
        phase_io = _PHASE_IO.get(self.phase, {})

        # read_input_files — only for phases that need raw files
        if phase_io.get("reads_input_dir"):
            tools.append({
                "name": "read_input_files",
                "description": (
                    "Read the raw input files from the workspace input directory. "
                    "Returns a list of files with their names, types, and extracted text content. "
                    "Use this to understand the uploaded project materials."
                ),
                "parameters": {
                    "max_chars": "Maximum chars to extract per file (default: 10000)",
                },
            })

        # read_artifact — read prior phase outputs
        tools.append({
            "name": "read_artifact",
            "description": (
                "Read an existing JSON artifact from a prior phase. "
                f"Available artifacts: {', '.join(phase_io.get('inputs', []))}."
            ),
            "parameters": {
                "name": "Artifact name (e.g., 'source_summary', 'outline')",
            },
        })

        # compose_artifact — LLM provides instructions, tool generates structured JSON
        tools.append({
            "name": "compose_artifact",
            "description": (
                "Compose the output artifact as a JSON object. "
                "YOU provide the complete JSON content — this is where your reasoning "
                "materializes into structured output. Include all required fields. "
                f"The output artifact is: {phase_io.get('output', 'unknown')}."
            ),
            "parameters": {
                "content": "The complete JSON object for the artifact (dict)",
            },
        })

        # write_artifact — save to disk
        tools.append({
            "name": "write_artifact",
            "description": "Write the composed artifact to the workspace.",
            "parameters": {
                "name": "Artifact name",
                "payload": "The JSON data to write",
            },
        })

        # validate_output
        tools.append({
            "name": "validate_output",
            "description": (
                "Validate that the phase's output artifact exists and is well-formed JSON "
                "with the expected structure."
            ),
            "parameters": {},
        })

        return tools

    def _get_operation_tools(self) -> list[dict]:
        """Tools for operation phases — template search, PPTX assembly, etc."""
        tools = [
            {
                "name": "read_artifact",
                "description": "Read an existing JSON artifact from the workspace.",
                "parameters": {"name": "Artifact name"},
            },
        ]

        if self.phase == "template_matching":
            tools.append({
                "name": "search_templates",
                "description": (
                    "Search the template index for templates matching the given "
                    "query. Returns ranked results with scores. "
                    "The query should include domain, audience, and tone terms "
                    "derived from the outline."
                ),
                "parameters": {
                    "query": "Search query string (e.g., 'professional software product blue')",
                    "top_k": "Number of top results to return (default: 3)",
                },
            })
        elif self.phase == "ppt_assembly":
            tools.append({
                "name": "assemble_pptx",
                "description": (
                    "Assemble the final PowerPoint file using zone_id precise matching. "
                    "Base mappings are built automatically from slide_contents zone_ids. "
                    "Pass optional 'adaptations' to shorten or merge content that "
                    "overflows its template zone — ONLY 'content' can be changed, "
                    "zone_id and formatting are immutable. "
                    "The result includes overflow_zones if any content may not fit."
                ),
                "parameters": {
                    "adaptations": (
                        "Optional: {slide_index: {content_type: {content: ...}}}. "
                        "Only change 'content' values — zone_id is auto-assigned "
                        "and cannot be overridden. Example: "
                        '{"3": {"bullets": {"content": ["短标题1", "短标题2"]}}}'
                    ),
                    "image_mappings": (
                        "Optional: {slide_index: {zone_id: image_path}} "
                        "override image placements"
                    ),
                },
            })
            tools.append({
                "name": "check_file",
                "description": "Check if a file exists and return its size.",
                "parameters": {"path": "File path (absolute or relative to workspace)"},
            })

        tools.extend([
            {
                "name": "write_artifact",
                "description": "Write a JSON artifact to the workspace.",
                "parameters": {"name": "Artifact name", "payload": "JSON data"},
            },
            {
                "name": "validate_output",
                "description": "Validate the phase's output artifact.",
                "parameters": {},
            },
        ])

        return tools

    def _get_skill_autonomous_tools(self) -> list[dict]:
        """Tools for skill-autonomous phases — shell, files, artifacts."""
        return [
            {
                "name": "run_shell_command",
                "description": (
                    "Execute a shell command and return stdout/stderr. "
                    "Use this to run the Skill's scripts. The command runs with the workspace as cwd."
                ),
                "parameters": {
                    "command": "The shell command to execute",
                    "timeout": "Timeout in seconds (default: 120)",
                    "cwd": "Working directory (default: workspace root)",
                },
            },
            {
                "name": "check_file_exists",
                "description": "Check if a file exists and return its metadata.",
                "parameters": {"path": "Path to check (relative to workspace or absolute)"},
            },
            {
                "name": "list_directory",
                "description": "List contents of a directory.",
                "parameters": {"path": "Directory path"},
            },
            {
                "name": "read_artifact",
                "description": "Read an existing JSON artifact from the workspace.",
                "parameters": {"name": "Artifact name"},
            },
            {
                "name": "write_artifact",
                "description": "Write a JSON artifact to the workspace.",
                "parameters": {"name": "Artifact name", "payload": "JSON data"},
            },
            {
                "name": "validate_output",
                "description": "Validate the phase's output artifact.",
                "parameters": {},
            },
        ]

    # ── Tool dispatch ──

    def execute_tool(self, tool_call: ToolCall) -> ToolResult:
        name = tool_call.tool_name
        args = tool_call.arguments

        # Phase 6: use ToolRegistry (RBAC enforced by registry)
        if self._registry is not None:
            try:
                return self._registry.execute(tool_call, role="worker")
            except Exception as e:
                return ToolResult(call_id=tool_call.call_id, output=None, success=False, error=str(e))

        # Legacy: RBAC via allowlist
        if name not in _WORKER_ALLOWLIST:
            return ToolResult(call_id=tool_call.call_id, output=None, success=False,
                              error=f"Tool not in worker allowlist: {name}")

        dispatch = {
            "read_input_files": lambda: self._read_input_files(int(args.get("max_chars", 10000))),
            "read_artifact": lambda: self._read_artifact(args.get("name", "")),
            "compose_artifact": lambda: self._compose_artifact(args.get("content", {})),
            "write_artifact": lambda: self._write_artifact(args.get("name", ""), args.get("payload", {})),
            "validate_output": lambda: self._validate_output(),
            "search_templates": lambda: self._search_templates(args.get("query", ""), int(args.get("top_k", 3))),
            "assemble_pptx": lambda: self._assemble_pptx(
                adaptations=args.get("adaptations"),
                image_mappings=args.get("image_mappings"),
            ),
            "check_file": lambda: self._check_file_exists(args.get("path", "")),
            "run_shell_command": lambda: self._run_shell_command(args.get("command", ""), int(args.get("timeout", 120)), args.get("cwd")),
            "check_file_exists": lambda: self._check_file_exists(args.get("path", "")),
            "list_directory": lambda: self._list_directory(args.get("path", "")),
            # Legacy: keep execute_phase for backward compat with existing tests
            "execute_phase": lambda: self._execute_phase(),
        }

        handler = dispatch.get(name)
        if handler:
            return handler()

        return ToolResult(
            call_id=tool_call.call_id,
            output=None, success=False,
            error=f"Unknown tool: {name}",
        )

    def parse_llm_response(self, result: LLMResult) -> tuple[str, list[ToolCall], bool]:
        text = result.text
        try:
            data, _warnings = repair_json(text)
        except Exception:
            return text, [], False
        if not isinstance(data, dict):
            return text, [], False
        if data.get("done") is True:
            return text, [], True
        tool_name = data.get("tool")
        if tool_name:
            tc = ToolCall(
                tool_name=tool_name,
                arguments=data.get("arguments", {}),
                call_id=f"tc_{len(self.turns) + 1}",
            )
            return text, [tc], False
        return text, [], False

    # ── Tool implementations ──

    def _read_input_files(self, max_chars: int = 10000) -> ToolResult:
        """Read raw input files from the workspace — LLM's first step for doc analysis."""
        from ppt_agent.tools.documents import extract_text

        files = list(self.workspace.input_dir.rglob("*"))
        files = [f for f in files if f.is_file()]

        results = []
        image_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

        for f in files:
            entry = {"name": f.name, "type": f.suffix, "path": str(f.relative_to(self.workspace.root))}
            if f.suffix.lower() in image_suffixes:
                entry["category"] = "image"
                entry["size_bytes"] = f.stat().st_size
            else:
                entry["category"] = "text"
                try:
                    text = extract_text(f)
                    entry["content"] = text[:max_chars] if text else ""
                    entry["total_chars"] = len(text) if text else 0
                except Exception as e:
                    entry["content"] = ""
                    entry["error"] = str(e)
            results.append(entry)

        return ToolResult(
            call_id="", success=True,
            output={"file_count": len(results), "files": results},
        )

    def _read_artifact(self, name: str) -> ToolResult:
        try:
            from ppt_agent.coordinator.phase_state import load_artifact
            data = load_artifact(self.workspace, name)
            data_str = json.dumps(data, ensure_ascii=False)
            if len(data_str) > 5000:
                return ToolResult(
                    call_id="", success=True,
                    output={
                        "name": name, "size": len(data_str),
                        "preview": data_str[:3000] + "... [truncated]",
                        "keys": list(data.keys()) if isinstance(data, dict) else None,
                    },
                )
            return ToolResult(call_id="", output=data, success=True)
        except FileNotFoundError:
            return ToolResult(call_id="", output=None, success=False, error=f"Artifact '{name}' not found")
        except Exception as e:
            return ToolResult(call_id="", output=None, success=False, error=str(e))

    def _compose_artifact(self, content: dict) -> ToolResult:
        """Accept the LLM's composed artifact content.

        The LLM provides the COMPLETE JSON object. This tool validates
        basic structure and returns it for the LLM to review before writing.
        """
        if not isinstance(content, dict):
            return ToolResult(
                call_id="", output=None, success=False,
                error="content must be a JSON object (dict)",
            )
        if not content:
            return ToolResult(
                call_id="", output=None, success=False,
                error="content is empty — provide the full artifact data",
            )

        # Basic validation: check required keys for known phase outputs
        phase_io = _PHASE_IO.get(self.phase, {})
        output_name = phase_io.get("output", "")
        warnings = []

        if output_name == "source_summary":
            for key in ["project_name", "domain", "product_capabilities"]:
                if key not in content:
                    warnings.append(f"Missing recommended key: {key}")
        elif output_name == "outline":
            if "slides" not in content:
                warnings.append("Missing required key: slides")
            if "meta" not in content:
                warnings.append("Missing required key: meta")
        elif output_name == "slide_design_plan":
            if "theme_profile" not in content:
                warnings.append("Missing required key: theme_profile")
            if "slides" not in content:
                warnings.append("Missing required key: slides")
        elif output_name == "slide_contents":
            if "slides" not in content:
                warnings.append("Missing required key: slides")

        return ToolResult(
            call_id="", success=True,
            output={
                "composed": True,
                "artifact_name": output_name,
                "keys": list(content.keys()),
                "size_estimate": len(json.dumps(content, ensure_ascii=False)),
                "warnings": warnings,
                "data": content,  # Pass back so LLM can review
            },
        )

    def _write_artifact(self, name: str, payload: dict) -> ToolResult:
        try:
            from ppt_agent.coordinator.phase_state import write_artifact
            path = write_artifact(self.workspace, name, payload)
            return ToolResult(
                call_id="", success=True,
                output={"written": str(path), "name": name},
            )
        except Exception as e:
            return ToolResult(call_id="", output=None, success=False, error=str(e))

    def _validate_output(self) -> ToolResult:
        phase_artifacts = {
            "document_analysis": "source_summary",
            "outline_generation": "outline",
            "template_matching": "selected_template",
            "design_planning": "slide_design_plan",
            "content_mapping": "slide_contents",
            "visual_generation": "image_generation_report",
            "ppt_assembly": None,
            "verification": "validation_report",
        }

        if self.phase == "ppt_assembly":
            pptx_path = self.workspace.root / "final.pptx"
            if pptx_path.exists():
                return ToolResult(
                    call_id="", success=True,
                    output={"valid": True, "path": str(pptx_path), "size": pptx_path.stat().st_size},
                )
            return ToolResult(
                call_id="", success=False, output={"valid": False},
                error="final.pptx not found",
            )

        artifact_name = phase_artifacts.get(self.phase)
        if not artifact_name:
            return ToolResult(
                call_id="", success=False, output={"valid": False},
                error=f"Unknown phase: {self.phase}",
            )

        artifact_path = self.workspace.artifact_path(artifact_name)
        if artifact_path.exists():
            try:
                data = json.loads(artifact_path.read_text(encoding="utf-8"))
                return ToolResult(
                    call_id="", success=True,
                    output={
                        "valid": True, "path": str(artifact_path),
                        "keys": list(data.keys()) if isinstance(data, dict) else None,
                    },
                )
            except json.JSONDecodeError as e:
                return ToolResult(
                    call_id="", success=False,
                    output={"valid": False}, error=f"Invalid JSON: {e}",
                )
        return ToolResult(
            call_id="", success=False,
            output={"valid": False, "missing": artifact_name},
            error=f"Artifact '{artifact_name}' not found",
        )

    # ── Operation-phase tools ──

    def _search_templates(self, query_text: str, top_k: int = 3) -> ToolResult:
        """Search templates using 3-layer chunking + weighted aggregation."""
        try:
            from ppt_agent.retrieval.query_router import query
            from ppt_agent.retrieval.template_index import load_template_chunks

            chunks = load_template_chunks()
            items = [
                {"id": c["chunk_id"], "template_id": c["template_id"],
                 "text": c["text"], "chunk_type": c["chunk_type"],
                 "weight": c.get("weight", 0.33)}
                for c in chunks
            ]
            ranked = query(items, query_text, top_k=max(30, len(items)))

            # Weighted aggregation per template
            tpl_scores: dict[str, float] = {}
            for r in ranked:
                tid = r.get("template_id", "")
                if not tid: continue
                w = r.get("weight", 0.33)
                tpl_scores[tid] = tpl_scores.get(tid, 0.0) + r.get("score", 0) * w
            sorted_tpl = sorted(tpl_scores.items(), key=lambda x: -x[1])
            results = [{"template_id": tid, "score": round(s, 4)} for tid, s in sorted_tpl[:top_k]]

            return ToolResult(
                call_id="", success=True,
                output={
                    "query": query_text,
                    "result_count": len(results),
                    "results": results,
                },
            )
        except Exception as e:
            return ToolResult(call_id="", output=None, success=False, error=str(e))

    def _assemble_pptx(self, adaptations: dict | None = None,
                       image_mappings: dict | None = None) -> ToolResult:
        """Assemble the final PPTX with zone_id precise matching + LLM adaptations.

        1. Builds base mappings from slide_contents zone_ids (content_mapping's work).
        2. Computes overflow report — zones where text exceeds template shape capacity.
        3. Merges LLM *adaptations* (content-only changes, zone_id is immutable).
        4. Assembles via ``write_pptx_from_mapping``.
        5. Returns overflow info so the LLM can do a second pass if needed.
        """
        try:
            from ppt_agent.assembly.ppt_writer import write_pptx, write_pptx_from_mapping
            from ppt_agent.coordinator.phase_state import load_artifact
            from ppt_agent.workers.ppt_assembler import (
                _build_mappings_from_slide_contents,
                _build_overflow_report,
                _find_template_pptx,
                _merge_adaptations,
            )

            slide_contents = load_artifact(self.workspace, "slide_contents")
            output_path = self.workspace.root / "final.pptx"
            template_path = _find_template_pptx(self.workspace)

            if template_path:
                template_zones = load_artifact(self.workspace, "template_zones")

                # Layer 1: deterministic base mappings (zone_id immutable)
                base_mappings, auto_images = _build_mappings_from_slide_contents(slide_contents)
                if image_mappings is None:
                    image_mappings = auto_images

                # Layer 2: compute overflow before merging
                overflow = _build_overflow_report(slide_contents, template_zones)

                # Layer 3: merge LLM adaptations (content only)
                mappings = _merge_adaptations(
                    {k: dict(v) for k, v in base_mappings.items()},
                    adaptations,
                )

                logger.info(
                    "Agent assembly: template=%s, %d slides, %d overflow zones, "
                    "adaptations=%s",
                    template_path.name, len(mappings), len(overflow),
                    "provided" if adaptations else "none",
                )

                write_pptx_from_mapping(
                    template_path=template_path,
                    template_zones=template_zones,
                    mappings=mappings,
                    image_mappings=image_mappings or {},
                    slide_contents=slide_contents,
                    output_path=output_path,
                    workspace_root=self.workspace.root,
                )

                result_output = {
                    "assembled": True,
                    "path": str(output_path),
                    "size_bytes": output_path.stat().st_size,
                    "mode": "zone_id_precise",
                    "slides_mapped": len(mappings),
                }

                if overflow:
                    result_output["overflow_zones"] = overflow
                    result_output["overflow_count"] = len(overflow)
                    result_output["hint"] = (
                        f"{len(overflow)} zone(s) may have content overflow. "
                        "Call assemble_pptx again with 'adaptations' to shorten "
                        "or merge overflowing content. Only 'content' can be "
                        "changed — zone_id and formatting are immutable."
                    )
            else:
                logger.info("No template PPTX found — blank assembly")
                write_pptx(slide_contents, output_path, workspace_root=self.workspace.root)
                result_output = {
                    "assembled": True,
                    "path": str(output_path),
                    "size_bytes": output_path.stat().st_size,
                    "mode": "blank_slides",
                }

            if output_path.exists():
                return ToolResult(call_id="", success=True, output=result_output)
            return ToolResult(
                call_id="", success=False, output={"assembled": False},
                error="write_pptx completed but final.pptx not found",
            )
        except Exception as e:
            return ToolResult(call_id="", output=None, success=False, error=str(e))

    # ── Shared tools ──

    def _check_file_exists(self, path: str) -> ToolResult:
        if not path:
            return ToolResult(call_id="", output=None, success=False, error="Empty path")
        p = Path(path)
        if not p.is_absolute():
            p = self.workspace.root / p
        if p.exists():
            info = {"path": str(p), "exists": True, "is_file": p.is_file(), "is_dir": p.is_dir()}
            if p.is_file():
                info["size_bytes"] = p.stat().st_size
            return ToolResult(call_id="", output=info, success=True)
        return ToolResult(call_id="", output={"path": str(p), "exists": False}, success=True)

    def _list_directory(self, path: str) -> ToolResult:
        if not path:
            return ToolResult(call_id="", output=None, success=False, error="Empty path")
        p = Path(path)
        if not p.is_absolute():
            p = self.workspace.root / p
        if not p.exists():
            return ToolResult(call_id="", output=None, success=False, error=f"Not found: {p}")
        if not p.is_dir():
            return ToolResult(call_id="", output=None, success=False, error=f"Not a directory: {p}")
        entries = []
        try:
            for item in sorted(p.iterdir()):
                entry = {"name": item.name, "type": "dir" if item.is_dir() else "file"}
                if item.is_file():
                    entry["size"] = item.stat().st_size
                entries.append(entry)
        except PermissionError as e:
            return ToolResult(call_id="", output=None, success=False, error=f"Permission denied: {e}")
        return ToolResult(
            call_id="", success=True,
            output={"path": str(p), "count": len(entries), "entries": entries[:100]},
        )

    def _run_shell_command(self, command: str, timeout: int = 120, cwd: str | None = None) -> ToolResult:
        if not command.strip():
            return ToolResult(call_id="", output=None, success=False, error="Empty command")

        # ── Phase 4.4: Sandbox security checks ─────────────────
        _BLOCKED = _BLOCKED_PATTERNS
        cmd_lower = command.lower()
        for pattern in _BLOCKED:
            if pattern in cmd_lower:
                return ToolResult(
                    call_id="", output=None, success=False,
                    error=f"Blocked dangerous command pattern: {pattern}",
                )

        # Enforce working directory within workspace
        work_dir = Path(cwd or str(self.workspace.root)).resolve()
        ws_root = self.workspace.root.resolve()
        if ws_root not in work_dir.parents and work_dir != ws_root:
            return ToolResult(
                call_id="", output=None, success=False,
                error=f"Working directory outside workspace: {work_dir}",
            )

        # Environment variable whitelist — pass through commonly needed vars
        _API_KEY_VARS = {
            k: v for k, v in os.environ.items()
            if k.endswith("_API_KEY") or k.endswith("_API_TOKEN")
        }
        safe_env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "LANG": os.environ.get("LANG", "en_US.UTF-8"),
            "PYTHONUNBUFFERED": "1",
            "WORKSPACE_ROOT": str(ws_root),
            "TMPDIR": os.environ.get("TMPDIR", os.environ.get("TEMP", os.environ.get("TMP", ""))),
            "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
            **_API_KEY_VARS,
        }
        # ───────────────────────────────────────────────────────

        logger.info("Worker %s shell: %s", self.phase, command[:200])
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=str(work_dir), env=safe_env,
            )
            output = {
                "exit_code": result.returncode,
                "stdout": result.stdout[-3000:] if result.stdout else "",
                "stderr": result.stderr[-1000:] if result.stderr else "",
            }
            return ToolResult(
                call_id="", output=output,
                success=result.returncode == 0,
                error=f"Exit code {result.returncode}" if result.returncode != 0 else None,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(call_id="", output={"timeout": timeout}, success=False, error=f"Timed out after {timeout}s")
        except Exception as e:
            return ToolResult(call_id="", output=None, success=False, error=str(e))

    # Legacy compat
    def _execute_phase(self) -> ToolResult:
        """Legacy tool — kept for backward compatibility with tests."""
        try:
            output = self._run_deterministic_worker()
            return ToolResult(
                call_id="", success=True,
                output={"phase": self.phase, "output_path": str(output), "status": "success"},
            )
        except Exception as e:
            return ToolResult(call_id="", output=None, success=False, error=f"Phase execution failed: {e}")
