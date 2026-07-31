"""AgentLoop — the core think→act→observe cycle.

This is the foundational abstraction for all agents in the system.
Both the Coordinator and each Worker run as independent AgentLoop instances.

The cycle:
    1. THINK  — LLM reasons over the current context (messages + memory + tools)
    2. ACT    — Execute the chosen tool / action
    3. OBSERVE — Append the result to conversation history
    4. REPEAT — Until the agent decides to stop (emits DONE) or hits max turns

Design principles (from AGENT_ARCHITECTURE.md §9):
    - Coordinator uses ONLY orchestration tools (AgentTool, TaskStopTool, etc.)
    - Workers use domain tools (FileSystem, JsonArtifact, etc.)
    - Each agent has its own context, memory, and tool set
    - The loop self-terminates on DONE signal, max turns, or error budget exceeded

Phase 2 (2026-07): added ConversationStore, Mailbox, ToolRegistry,
SkillLoader integration, L0-L4 message compression, and skill loading
as a built-in tool.  All new parameters default to None for backward
compatibility.
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, TYPE_CHECKING

from ppt_agent.context.compression import _llm_summarize
from ppt_agent.coordinator.event_bus import EventBus, EventType
from ppt_agent.llm.messages import LLMMessage, LLMResult
from ppt_agent.runtime.agent_context import agent_attribution

if TYPE_CHECKING:
    from ppt_agent.runtime.conversation_store import ConversationStore
    from ppt_agent.runtime.mailbox import Mailbox
    from ppt_agent.skills.loader import SkillLoader
    from ppt_agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# ── Token estimation constant ──────────────────────────────────────────
_CHARS_PER_TOKEN = 4


class AgentState(str, Enum):
    """Lifecycle states for an agent."""
    IDLE = "idle"
    THINKING = "thinking"
    ACTING = "acting"
    OBSERVING = "observing"
    DONE = "done"
    ERROR = "error"


class StopReason(str, Enum):
    """Why the agent loop terminated."""
    COMPLETED = "completed"           # Agent signaled DONE
    MAX_TURNS = "max_turns"           # Hit turn limit
    ERROR_BUDGET = "error_budget"     # Too many consecutive errors
    ABORTED = "aborted"               # External abort signal
    NO_ACTION = "no_action"           # LLM returned text without tool call


@dataclass
class ToolCall:
    """A tool invocation requested by the LLM."""
    tool_name: str
    arguments: dict[str, Any]
    call_id: str = ""


@dataclass
class ToolResult:
    """Result of executing a tool."""
    call_id: str
    output: Any
    success: bool = True
    error: str | None = None


@dataclass
class AgentTurn:
    """Record of a single think→act→observe cycle."""
    turn_number: int
    thought: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    duration_ms: int = 0
    stop_signal: bool = False
    terminal_error: bool = False


@dataclass
class AgentLoopResult:
    """Final result of an agent loop execution."""
    agent_id: str
    stop_reason: StopReason
    turns: list[AgentTurn] = field(default_factory=list)
    final_output: Any = None
    total_duration_ms: int = 0
    error: str | None = None


class AgentLoop(ABC):
    """Base class for all agents — implements the think→act→observe cycle.

    Subclasses implement:
        - build_system_prompt() → the agent's identity and instructions
        - get_available_tools() → tool descriptors for the LLM
        - execute_tool(tool_call) → actually run the tool
        - parse_llm_response(result) → extract tool calls or DONE signal
        - on_turn_complete(turn) → hook for logging, events, etc.

    Phase 2 additions (all optional, default None):
        - conversation_store — JSONL conversation persistence
        - mailbox — inter-agent message passing
        - tool_registry — centralised tool RBAC + execution
        - skill_loader — progressive skill loading
        - context_window_limit — used by _compress_messages()

    Phase 3 additions:
        - job_root — when set, every LLM call is audited to model_calls.jsonl
    """

    def __init__(
        self,
        agent_id: str,
        llm_client,
        *,
        max_turns: int = 20,
        max_consecutive_errors: int = 3,
        compression_level: int = 0,
        event_bus: EventBus | None = None,
        # ── Phase 2 additions ──
        conversation_store: ConversationStore | None = None,
        mailbox: Mailbox | None = None,
        tool_registry: ToolRegistry | None = None,
        skill_loader: SkillLoader | None = None,
        context_window_limit: int = 128_000,
        # ── Phase 3 additions ──
        job_root: Path | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.llm_client = llm_client
        self.max_turns = max_turns
        self.max_consecutive_errors = max_consecutive_errors
        self.compression_level = compression_level
        self.event_bus = event_bus

        # Phase 2
        self.conversation_store = conversation_store
        self.mailbox = mailbox
        self.tool_registry = tool_registry
        self.skill_loader = skill_loader
        self.context_window_limit = context_window_limit

        # Phase 3
        self.job_root = job_root

        self.state = AgentState.IDLE
        self.messages: list[LLMMessage] = []
        self.turns: list[AgentTurn] = []
        self._consecutive_errors = 0
        self._no_action_count = 0
        self._max_no_action_retries = 3
        self._aborted = False
        self._loaded_skills: set[str] = set()

    def abort(self) -> None:
        """Signal the agent to stop at the next opportunity."""
        self._aborted = True

    def run(self, task_prompt: str, context: dict | None = None) -> AgentLoopResult:
        """Execute the full agent loop.

        Args:
            task_prompt: The task description / user request.
            context: Optional structured context (artifacts, prior results, etc.)

        Returns:
            AgentLoopResult with all turns and final output.
        """
        start_time = time.monotonic()

        # ── Reset per-run state (re-entrant safety) ────────────
        self.turns = []
        self._consecutive_errors = 0
        self._no_action_count = 0

        with agent_attribution(self.agent_id):
            # Initialize messages
            system_prompt = self.build_system_prompt()
            self.messages = [LLMMessage.system(system_prompt)]

            # ── Phase 2: Restore conversation history ──────────────
            if self.conversation_store is not None:
                prior = self.conversation_store.load_messages()
                if prior:
                    # Filter out system-role messages to avoid duplicate
                    # system prompts from prior runs corrupting context.
                    non_system = [m for m in prior if m.role != "system"]
                    if non_system:
                        self.messages.extend(non_system)

            # Build the initial user message with context
            user_text = task_prompt
            if context:
                compressed = self._compress_context_dict(context)
                context_str = json.dumps(compressed, ensure_ascii=False, indent=2)
                user_text = f"{task_prompt}\n\n## Context\n\n{context_str}"
            self.messages.append(LLMMessage.user(user_text))

            # Main loop
            final_output = None
            stop_reason = StopReason.MAX_TURNS
            _last_count = len(self.messages)

            for turn_num in range(1, self.max_turns + 1):
                if self._aborted:
                    stop_reason = StopReason.ABORTED
                    break

                turn = self._execute_turn(turn_num)
                self.turns.append(turn)

                if turn.terminal_error:
                    stop_reason = StopReason.ERROR_BUDGET
                    final_output = turn.thought
                    break

                # ── Phase 2: Persist new messages ────────────────
                if self.conversation_store is not None:
                    for msg in self.messages[_last_count:]:
                        self.conversation_store.append(
                            msg,
                            node_id=f"n{turn_num}",
                            agent_id=self.agent_id,
                        )
                    _last_count = len(self.messages)

                # Check for DONE signal
                if turn.stop_signal:
                    stop_reason = StopReason.COMPLETED
                    final_output = self._extract_final_output(turn)
                    break

                # No tool calls and no stop signal → agent produced
                # reasoning text without a tool call.  Give it a chance to
                # recover by appending a continuation prompt and retrying
                # (up to _max_no_action_retries).  Prevents immediate
                # NO_ACTION death on pure-thinking turns while guarding
                # against infinite silent loops.
                if not turn.tool_calls:
                    self._no_action_count += 1
                    if self._no_action_count >= self._max_no_action_retries:
                        stop_reason = StopReason.NO_ACTION
                        final_output = turn.thought
                        break
                    logger.info(
                        "Agent %s turn %d: no tool call (retry %d/%d), appending continuation prompt.",
                        self.agent_id, turn_num,
                        self._no_action_count, self._max_no_action_retries,
                    )
                    self.messages.append(LLMMessage.user(
                        "Please take an action: call a tool, or signal completion "
                        'with {"done": true}. Do not output reasoning-only JSON.'
                    ))
                    self._consecutive_errors = 0  # reasoning is not an error
                    continue

                # Check error budget
                if self._consecutive_errors >= self.max_consecutive_errors:
                    stop_reason = StopReason.ERROR_BUDGET
                    break

            elapsed = int((time.monotonic() - start_time) * 1000)

            result = AgentLoopResult(
                agent_id=self.agent_id,
                stop_reason=stop_reason,
                turns=self.turns,
                final_output=final_output,
                total_duration_ms=elapsed,
            )

            if stop_reason == StopReason.ERROR_BUDGET:
                result.error = f"Agent hit error budget after {self._consecutive_errors} consecutive errors"
            elif stop_reason == StopReason.MAX_TURNS:
                result.error = f"Agent hit max turn limit ({self.max_turns})"

            self.state = AgentState.DONE if stop_reason == StopReason.COMPLETED else AgentState.ERROR
            return result

    def _emit(self, event_type: str, **kwargs) -> None:
        """Emit an event to the event bus if one is configured."""
        if self.event_bus is not None:
            self.event_bus.emit(
                event_type,
                message=kwargs.pop("message", ""),
                metadata={"agent_id": self.agent_id, **kwargs},
            )

    def _execute_turn(self, turn_num: int) -> AgentTurn:
        """Execute a single think→act→observe cycle."""
        turn_start = time.monotonic()
        turn = AgentTurn(turn_number=turn_num)

        # ── Phase 2: Check mailbox before thinking ───────────────
        self._check_mailbox()
        if self._aborted:
            turn.thought = "[Aborted by SHUTDOWN]"
            turn.stop_signal = True
            turn.duration_ms = int((time.monotonic() - turn_start) * 1000)
            return turn

        # ── THINK ──
        self.state = AgentState.THINKING
        self._emit(
            EventType.LLM_CALL_START,
            message=f"LLM call for turn {turn_num}",
            turn=turn_num,
        )
        try:
            llm_result = self._call_llm()
        except Exception as e:
            logger.error("Agent %s LLM call failed on turn %d: %s", self.agent_id, turn_num, e)
            self._consecutive_errors += 1
            from ppt_agent.llm.client import is_non_retryable_llm_error
            turn.terminal_error = is_non_retryable_llm_error(str(e))
            self._emit(
                EventType.ERROR,
                message=f"LLM call failed: {e}",
                turn=turn_num,
            )
            turn.thought = f"[LLM ERROR] {e}"
            turn.duration_ms = int((time.monotonic() - turn_start) * 1000)
            # Add error as assistant message for self-correction
            self.messages.append(LLMMessage.assistant(f"[Internal error: {e}. I will retry.]"))
            return turn

        # Parse the LLM response
        try:
            thought, tool_calls, done = self.parse_llm_response(llm_result)
        except Exception as e:
            logger.error("Agent %s parse_llm_response failed on turn %d: %s", self.agent_id, turn_num, e)
            self._consecutive_errors += 1
            self._emit(EventType.ERROR, message=f"Parse failed: {e}", turn=turn_num)
            thought = f"[PARSE ERROR] {e}"
            tool_calls = []
            done = False
            self.messages.append(LLMMessage.assistant(f"[Internal parse error: {e}. I will retry.]"))
            turn.thought = thought
            turn.tool_calls = tool_calls
            turn.stop_signal = done
            turn.duration_ms = int((time.monotonic() - turn_start) * 1000)
            return turn
        turn.thought = thought
        turn.tool_calls = tool_calls
        turn.stop_signal = done

        # Emit LLM result event
        self._emit(
            EventType.LLM_CALL_RESULT,
            message=f"LLM returned on turn {turn_num}",
            turn=turn_num,
            tool_call_count=len(tool_calls),
            done=done,
        )

        # Add assistant message
        self.messages.append(LLMMessage.assistant(thought if thought else llm_result.text))

        if done or not tool_calls:
            turn.duration_ms = int((time.monotonic() - turn_start) * 1000)
            self.on_turn_complete(turn)
            return turn

        # ── ACT + OBSERVE ──
        self.state = AgentState.ACTING
        for tc in tool_calls:
            # ── Phase 2: Handle skill loading as a built-in ─────
            if tc.tool_name == "load_skill":
                result = self._load_skill_tool(tc.arguments.get("skill_name", ""))
                result.call_id = tc.call_id
                turn.tool_results.append(result)
                if result.success:
                    self._consecutive_errors = 0
                else:
                    self._consecutive_errors += 1
                self.messages.append(LLMMessage.user(
                    f"[Tool Result: load_skill]\n{json.dumps(result.output, ensure_ascii=False)}"
                ))
                continue

            # Emit tool call start
            self._emit(
                EventType.TOOL_CALL_START,
                message=f"Calling tool {tc.tool_name}",
                turn=turn_num,
                tool_name=tc.tool_name,
                call_id=tc.call_id,
            )
            try:
                result = self.execute_tool(tc)
                turn.tool_results.append(result)
                if result.success:
                    self._consecutive_errors = 0
                else:
                    self._consecutive_errors += 1

                # Emit tool call result
                self._emit(
                    EventType.TOOL_CALL_RESULT,
                    message=(
                        f"Tool {tc.tool_name} succeeded"
                        if result.success
                        else f"Tool {tc.tool_name} rejected: {result.error}"
                    ),
                    turn=turn_num,
                    tool_name=tc.tool_name,
                    call_id=tc.call_id,
                    success=result.success,
                    error=result.error,
                )

                if result.success:
                    result_text = json.dumps(result.output, ensure_ascii=False) if isinstance(result.output, (dict, list)) else str(result.output)
                    self.messages.append(LLMMessage.user(
                        f"[Tool Result: {tc.tool_name}]\n{result_text}"
                    ))
                else:
                    self.messages.append(LLMMessage.user(
                        f"[Tool Error: {tc.tool_name}]\n{result.error or 'Tool request was rejected.'}\n\n"
                        "Choose a legal action based on the current workflow state."
                    ))
            except Exception as e:
                logger.warning("Agent %s tool %s failed: %s", self.agent_id, tc.tool_name, e)
                self._consecutive_errors += 1
                error_result = ToolResult(
                    call_id=tc.call_id,
                    output=None,
                    success=False,
                    error=str(e),
                )
                turn.tool_results.append(error_result)

                # Emit tool error event
                self._emit(
                    EventType.TOOL_CALL_RESULT,
                    message=f"Tool {tc.tool_name} failed: {e}",
                    turn=turn_num,
                    tool_name=tc.tool_name,
                    call_id=tc.call_id,
                    success=False,
                    error=str(e),
                )

                # Feed error back for self-correction
                self.messages.append(LLMMessage.user(
                    f"[Tool Error: {tc.tool_name}]\n{e}\n\nPlease fix the issue and try again."
                ))

        self.state = AgentState.OBSERVING
        turn.duration_ms = int((time.monotonic() - turn_start) * 1000)
        self.on_turn_complete(turn)
        return turn

    def _call_llm(self) -> LLMResult:
        """Call the LLM with current messages and available tools.

        Retries up to 2 times with exponential backoff on transient failures
        (network errors, rate limits).  Persistent errors (e.g. invalid API
        key) are raised immediately.
        """
        # ── Phase 2: Compress messages before LLM call ───────────
        self._compress_messages()

        tools = self.get_available_tools()

        # Build the tool descriptions into the system prompt context
        if tools:
            tool_desc = self._format_tools_for_prompt(tools)
            augmented_messages = list(self.messages)
            # Add tool awareness to the system prompt
            if not any("Available tools:" in (m.content[0].text or "") for m in augmented_messages if m.role == "system"):
                sys_msg = augmented_messages[0]
                augmented_text = (sys_msg.content[0].text or "") + "\n\n" + tool_desc
                augmented_messages[0] = LLMMessage.system(augmented_text)
        else:
            augmented_messages = self.messages

        last_error = None
        for attempt in range(3):
            try:
                from ppt_agent.llm.client import (
                    LLMClient,
                    is_non_retryable_llm_error,
                )
                if isinstance(self.llm_client, LLMClient):
                    result = self.llm_client.generate(
                        augmented_messages,
                        temperature=0.3,
                        max_tokens=4096,
                    )
                else:
                    result = self.llm_client.provider.generate(
                        augmented_messages,
                        temperature=0.3,
                        max_tokens=4096,
                    )
            except Exception as exc:
                last_error = exc
                if is_non_retryable_llm_error(str(exc)):
                    raise RuntimeError(
                        f"LLM call failed with non-retryable error: {exc}"
                    ) from exc
                if attempt < 2:
                    wait_s = 2 ** attempt
                    logger.warning(
                        "Agent %s LLM call attempt %d/3 raised %s, retrying in %ds.",
                        self.agent_id, attempt + 1, type(exc).__name__, wait_s,
                    )
                    time.sleep(wait_s)
                    continue
                raise RuntimeError(
                    f"LLM call failed after 3 attempts: {exc}"
                ) from exc

            # ── Phase 3: Audit ALL calls before raising on failure ───
            self._log_model_call(result)

            if result.success:
                return result

            last_error = result.error
            if is_non_retryable_llm_error(result.error):
                raise RuntimeError(
                    "LLM returned a non-retryable provider error: "
                    f"{result.error}"
                )
            if attempt < 2:
                wait_s = 2 ** attempt
                logger.warning(
                    "Agent %s LLM call attempt %d/3 returned error: %s, retrying in %ds.",
                    self.agent_id, attempt + 1, result.error, wait_s,
                )
                time.sleep(wait_s)
            else:
                raise RuntimeError(
                    f"LLM returned error after 3 attempts: {result.error}"
                )

        # Unreachable — kept for type checker
        raise RuntimeError(f"LLM call failed: {last_error}")

    def _log_model_call(self, result: LLMResult) -> None:
        """Append a record to model_calls.jsonl (Phase 3 audit trail).

        Delegates to the existing ``llm/audit.py`` module to avoid
        duplicating audit logic.  Failures here MUST NOT crash the
        agent loop — a warning is logged and execution continues.
        """
        if self.job_root is None:
            return

        try:
            from ppt_agent.llm.audit import log_model_call

            # Extract a short prompt summary for the hash
            prompt_summary = ""
            if self.messages:
                last = self.messages[-1]
                prompt_summary = (last.content[0].text or "")[:200]

            log_model_call(
                job_root=Path(self.job_root),
                phase=self.agent_id,
                result=result,
                prompt_summary=prompt_summary,
            )
        except Exception:
            logger.warning(
                "Agent %s failed to write audit log (provider=%s, model=%s)",
                self.agent_id, result.provider, result.model, exc_info=True,
            )

    # ── Phase 2: New methods ────────────────────────────────────────

    def _check_mailbox(self) -> None:
        """Check for incoming inter-agent messages and inject into context.

        Called at the start of every turn (before THINK).
        Reads unread messages, sorts by priority, and injects them as
        ``[Message from <sender>]`` user messages.

        If a SHUTDOWN message (priority 1) is found, sets
        ``self._aborted = True``.
        """
        if self.mailbox is None:
            return

        try:
            unread = self.mailbox.read_unread()
        except Exception:
            logger.debug("Mailbox read failed for agent %s", self.agent_id, exc_info=True)
            return

        if not unread:
            return

        for msg in unread:
            priority = msg.get("priority", 3)
            sender = msg.get("sender", "unknown")
            text = msg.get("message", "")

            if priority == 1:  # SHUTDOWN
                logger.info("Agent %s received SHUTDOWN via mailbox", self.agent_id)
                self._aborted = True
                self.messages.append(LLMMessage.user(
                    f"[Message from {sender} — SHUTDOWN]\n{text}"
                ))
            else:
                artifact_refs = msg.get("artifact_refs", [])
                refs_str = f"\nArtifacts: {', '.join(artifact_refs)}" if artifact_refs else ""
                self.messages.append(LLMMessage.user(
                    f"[Message from {sender}]\n{text}{refs_str}"
                ))

        # Mark as read
        try:
            self.mailbox.mark_read()
        except Exception:
            logger.debug("Mailbox mark_read failed", exc_info=True)

    def _compress_messages(self) -> None:
        """L0-L4 graduated message compression (§3 Context Window Management).

        Applied before every LLM call.  Compresses ``self.messages``
        in-place when estimated token utilisation exceeds thresholds.

        Invariants (NEVER removed):
            - messages[0] (system prompt — agent.md + memory.md + session.md)
            - Last 2 messages
            - Messages whose content contains ``[Error`` or ``[Tool Error``
        """
        if not self.messages:
            return  # nothing to compress

        total = self._estimate_tokens(self.messages)
        utilization = total / max(self.context_window_limit, 1)

        if utilization < 0.6:
            return  # L0: no compression needed

        # Determine level
        if utilization >= 0.95:
            level = 4
        elif utilization >= 0.85:
            level = 3
        elif utilization >= 0.75:
            level = 2
        else:
            level = 1

        system_msg = self.messages[0]

        # Helper: check all content blocks for error markers (not just block[0])
        def _has_error(msg: LLMMessage) -> bool:
            for block in msg.content:
                t = block.text or ""
                if "[Error" in t or "[Tool Error" in t:
                    return True
            return False

        error_msgs = [m for m in self.messages[1:-2] if _has_error(m)]
        # Build an id-based exclusion set for robust dedup (identity-based
        # ``not in`` is fragile when lists are independently constructed).
        error_ids = {id(m) for m in error_msgs}
        compressible = [m for m in self.messages[1:-2] if id(m) not in error_ids]

        # Save original positions so we can preserve interleaved order in L2
        _orig_pos = {id(m): i for i, m in enumerate(self.messages)}

        if level == 1:
            # L1: truncate large tool outputs
            for msg in compressible:
                text = msg.content[0].text or ""
                if "[Tool Result:" in text and len(text) > 2000:
                    truncated = text[:500] + f"\n... [truncated, original: {len(text)} chars]"
                    msg.content[0].text = truncated

        elif level == 2:
            # L2: LLM-summarise first half, keep second half verbatim
            midpoint = len(compressible) // 2
            old_msgs = compressible[:midpoint]
            keep_msgs = compressible[midpoint:]
            if old_msgs:
                summary = self._summarize_messages(old_msgs)
                summary_msg = LLMMessage.user(f"[Conversation Summary]\n{summary}")
                # Preserve original interleaved order of error + keep msgs
                merged = error_msgs + keep_msgs
                merged.sort(key=lambda m: _orig_pos.get(id(m), 0))
                recent = self.messages[-2:] if len(self.messages) >= 2 else self.messages[-1:]
                self.messages = [system_msg, summary_msg] + merged + recent

        elif level == 3:
            # L3: system + errors + last 4
            recent_n = self.messages[-4:] if len(self.messages) >= 4 else self.messages[1:]
            self.messages = [system_msg] + error_msgs + recent_n

        elif level == 4:
            # L4: emergency — system + last 2 only
            recent = self.messages[-2:] if len(self.messages) >= 2 else self.messages[-1:]
            # Avoid duplicating system prompt when it is already in recent
            if recent and recent[0] is system_msg:
                self.messages = recent
            else:
                self.messages = [system_msg] + recent

        self._emit(
            EventType.COMPRESSION_EVENT,
            message=f"Compressed messages to L{level}, utilization={utilization:.1%}",
        )

    def _load_skill_tool(self, skill_name: str) -> ToolResult:
        """Built-in tool: load a skill's full SKILL.md into the agent's context.

        On first load the SKILL.md content is appended to the system prompt.
        Subsequent calls return ``status: "already_loaded"``.

        Returns:
            ToolResult with success=True and status field.
        """
        if not skill_name:
            return ToolResult(
                call_id="", output=None, success=False,
                error="skill_name is required",
            )
        if self.skill_loader is None:
            return ToolResult(
                call_id="", output=None, success=False,
                error="No skill loader configured for this agent",
            )

        # Check local cache first, then query SkillLoader (which may be
        # shared across agents — is_loaded disambiguates "already seen"
        # from "not found").
        if skill_name in self._loaded_skills or (
            hasattr(self.skill_loader, "is_loaded") and self.skill_loader.is_loaded(skill_name)
        ):
            return ToolResult(
                call_id="", output={"status": "already_loaded", "skill": skill_name},
                success=True,
            )

        content = self.skill_loader.load_skill(skill_name)
        if content is None:
            # Ambiguous: could be "already loaded by another agent sharing
            # this loader" or "truly not found".  Report with context.
            return ToolResult(
                call_id="", output=None, success=False,
                error=f"Skill '{skill_name}' not found or already loaded by another agent",
            )

        # Append skill content to system prompt
        self.messages[0].content[0].text += f"\n\n## Loaded Skill: {skill_name}\n\n{content}"
        self._loaded_skills.add(skill_name)

        self._emit(
            EventType.SKILL_LOADED,
            message=f"Loaded skill: {skill_name}",
        )

        return ToolResult(
            call_id="",
            output={
                "status": "loaded",
                "skill": skill_name,
                "content_length": len(content),
            },
            success=True,
        )

    def _estimate_tokens(self, messages: list[LLMMessage]) -> int:
        """Heuristic token count: total characters / 4.

        Uses the same constant as the existing context compression layer.
        Future: replace with tiktoken for exact counts when needed.
        """
        total = 0
        for m in messages:
            for block in m.content:
                if block.text:
                    total += len(block.text)
        return max(total // _CHARS_PER_TOKEN, 0)

    @staticmethod
    def _compress_context_dict(context: dict) -> dict:
        """Lightweight compression for structured context dicts.

        Delegates to the existing context compressor when available,
        otherwise returns the dict unchanged.
        """
        try:
            from ppt_agent.context.compression import compress_context
            return compress_context(context, level=0)
        except Exception:
            return context

    def _summarize_messages(self, messages: list[LLMMessage]) -> str:
        """Summarize a list of messages using the LLM.

        Falls back to concatenated truncation if LLM is unavailable or fails.
        """
        combined = "\n".join(
            f"[{m.role}] {(m.content[0].text or '')[:500]}" for m in messages
        )
        try:
            summary = _llm_summarize(self.llm_client, combined, max_tokens=500)
            if summary:
                return summary
        except Exception:
            logger.debug("LLM summarization failed, falling back to truncation", exc_info=True)

        # Fallback: truncated concatenation
        return combined[:2000] + "\n... [truncated summary]"

    # ── Tool formatting (unchanged from original) ──────────────────

    def _format_tools_for_prompt(self, tools: list[dict]) -> str:
        """Format tool descriptors for injection into the system prompt."""
        lines = ["## Available Tools", ""]
        for tool in tools:
            name = tool.get("name", "unknown")
            desc = tool.get("description", "")
            params = tool.get("parameters", {})
            lines.append(f"### {name}")
            lines.append(f"{desc}")
            if params:
                lines.append(f"Parameters: {json.dumps(params, ensure_ascii=False)}")
            lines.append("")

        lines.append("## Response Format")
        lines.append("")
        lines.append("To use a tool, respond with a JSON block:")
        lines.append('```json')
        lines.append('{"tool": "<tool_name>", "arguments": {...}}')
        lines.append('```')
        lines.append("")
        lines.append('To signal completion, respond with:')
        lines.append('```json')
        lines.append('{"done": true, "result": <your_final_output>}')
        lines.append('```')
        lines.append("")
        lines.append("You may include reasoning text before the JSON block.")

        return "\n".join(lines)

    def _extract_final_output(self, turn: AgentTurn) -> Any:
        """Extract the final output from the last turn."""
        text = turn.thought
        try:
            from ppt_agent.llm.json_repair import repair_json
            data, _warnings = repair_json(text)
            if isinstance(data, dict) and "result" in data:
                return data["result"]
            return data
        except Exception:
            return text

    # ── Abstract methods to be implemented by subclasses ──

    @abstractmethod
    def build_system_prompt(self) -> str:
        """Build the system prompt for this agent."""
        ...

    @abstractmethod
    def get_available_tools(self) -> list[dict]:
        """Return tool descriptors available to this agent."""
        ...

    @abstractmethod
    def execute_tool(self, tool_call: ToolCall) -> ToolResult:
        """Execute a tool call and return the result."""
        ...

    @abstractmethod
    def parse_llm_response(self, result: LLMResult) -> tuple[str, list[ToolCall], bool]:
        """Parse the LLM response into thought, tool calls, and done signal.

        Returns:
            (thought_text, list_of_tool_calls, is_done)
        """
        ...

    def on_turn_complete(self, turn: AgentTurn) -> None:
        """Hook called after each turn completes. Override for logging/events."""
        logger.debug(
            "Agent %s turn %d complete: %d tool calls, done=%s",
            self.agent_id, turn.turn_number, len(turn.tool_calls), turn.stop_signal,
        )
