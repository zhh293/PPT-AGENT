"""content_mapping tools — map_slide_content delegates to worker."""

from ppt_agent.runtime.agent_loop import ToolResult
from ppt_agent.tools.registry import WORKER_AND_ABOVE, ToolDescriptor


def register_tools(registry, workspace, capability, llm_client=None):
    def map_content(call_id: str, arguments: dict) -> ToolResult:
        """Run content mapping in deterministic mode — agent already decided the content.

        Worker runs WITHOUT llm_client so it uses _fallback_mapping for execution.
        The agent (LLM) makes the zone decisions; the worker does position locking,
        formatting injection, and writes slide_contents.json.
        """
        try:
            from ppt_agent.workers.content_mapper import run as mapper_run
            force = arguments.get("force", False)
            # No llm_client passed — worker uses deterministic fallback
            output = mapper_run(workspace, force=force)
            return ToolResult(call_id=call_id, output={"path": str(output), "status": "ok"}, success=True)
        except Exception as e:
            return ToolResult(call_id=call_id, output=None, success=False, error=str(e))

    registry.register_tool(
        ToolDescriptor("map_slide_content", "content", WORKER_AND_ABOVE,
                       "Map outline content into template zones using the full mapping pipeline (position locking, formatting injection, visual data).",
                       {"force": {"type": "boolean", "default": False}}),
        map_content)
