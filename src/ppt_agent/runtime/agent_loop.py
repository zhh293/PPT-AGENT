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
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ppt_agent.context.compression import compress_context
from ppt_agent.coordinator.event_bus import EventBus, EventType
from ppt_agent.llm.messages import LLMMessage, LLMResult
from ppt_agent.runtime.agent_context import agent_attribution

logger = logging.getLogger(__name__)


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
    ABORTED = "aborted"              # External abort signal
    NO_ACTION = "no_action"          # LLM returned text without tool call


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
    ) -> None:
        self.agent_id = agent_id
        self.llm_client = llm_client
        self.max_turns = max_turns
        self.max_consecutive_errors = max_consecutive_errors
        self.compression_level = compression_level
        self.event_bus = event_bus

        self.state = AgentState.IDLE
        self.messages: list[LLMMessage] = []
        self.turns: list[AgentTurn] = []
        self._consecutive_errors = 0
        self._aborted = False

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

        with agent_attribution(self.agent_id):
            # Initialize messages
            system_prompt = self.build_system_prompt()
            self.messages = [LLMMessage.system(system_prompt)]

            # Build the initial user message with context
            user_text = task_prompt
            if context:
                compressed = compress_context(context, self.compression_level)
                context_str = json.dumps(compressed, ensure_ascii=False, indent=2)
                user_text = f"{task_prompt}\n\n## Context\n\n{context_str}"
            self.messages.append(LLMMessage.user(user_text))

            # Main loop
            final_output = None
            stop_reason = StopReason.MAX_TURNS

            for turn_num in range(1, self.max_turns + 1):
                if self._aborted:
                    stop_reason = StopReason.ABORTED
                    break

                turn = self._execute_turn(turn_num)
                self.turns.append(turn)

                # Check for DONE signal
                if turn.stop_signal:
                    stop_reason = StopReason.COMPLETED
                    final_output = self._extract_final_output(turn)
                    break

                # No tool calls and no stop signal → agent just produced text
                if not turn.tool_calls:
                    stop_reason = StopReason.NO_ACTION
                    final_output = turn.thought
                    break

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
        thought, tool_calls, done = self.parse_llm_response(llm_result)
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
                self._consecutive_errors = 0  # Reset on success

                # Emit tool call result
                self._emit(
                    EventType.TOOL_CALL_RESULT,
                    message=f"Tool {tc.tool_name} succeeded",
                    turn=turn_num,
                    tool_name=tc.tool_name,
                    call_id=tc.call_id,
                    success=True,
                )

                # Add tool result to messages
                result_text = json.dumps(result.output, ensure_ascii=False) if isinstance(result.output, (dict, list)) else str(result.output)
                self.messages.append(LLMMessage.user(
                    f"[Tool Result: {tc.tool_name}]\n{result_text}"
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
        """Call the LLM with current messages and available tools."""
        tools = self.get_available_tools()

        # Build the tool descriptions into the system prompt context
        if tools:
            tool_desc = self._format_tools_for_prompt(tools)
            # Inject tool descriptions as the last user-visible context
            # The LLM sees: system prompt + conversation + tool descriptions
            augmented_messages = list(self.messages)
            # Add tool awareness to the last user message or as a new one
            if not any("Available tools:" in (m.content[0].text or "") for m in augmented_messages if m.role == "system"):
                # Augment system prompt with tools
                sys_msg = augmented_messages[0]
                augmented_text = (sys_msg.content[0].text or "") + "\n\n" + tool_desc
                augmented_messages[0] = LLMMessage.system(augmented_text)
        else:
            augmented_messages = self.messages

        result = self.llm_client.provider.generate(
            augmented_messages,
            temperature=0.3,
            max_tokens=4096,
        )

        if not result.success:
            raise RuntimeError(f"LLM returned error: {result.error}")

        return result

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
        # Try to parse JSON from the thought
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
