"""ppt_assembly tools — assemble_pptx, check_file."""

from pathlib import Path

from ppt_agent.runtime.agent_loop import ToolResult
from ppt_agent.tools.registry import WORKER_AND_ABOVE, ToolDescriptor


def register_tools(registry, workspace, capability, llm_client=None):
    def assemble(args: dict) -> ToolResult:
        try:
            from ppt_agent.assembly.ppt_writer import write_pptx
            from ppt_agent.coordinator.phase_state import load_artifact
            slide_contents = load_artifact(workspace, "slide_contents")
            output = workspace.artifact_path("final").with_suffix(".pptx")
            path = write_pptx(slide_contents, str(output), workspace.root)
            return ToolResult(call_id="", output={"path": str(path)}, success=True)
        except Exception as e:
            return ToolResult(call_id="", output=None, success=False, error=str(e))

    registry.register_tool(
        ToolDescriptor("assemble_pptx", "assembly", WORKER_AND_ABOVE, "Assemble the final .pptx file."), assemble)

    def check(args: dict) -> ToolResult:
        p = Path(args.get("path", ""))
        if not p.is_absolute():
            p = workspace.root / p
        if p.exists():
            return ToolResult(call_id="", output={"exists": True, "path": str(p), "size": p.stat().st_size}, success=True)
        return ToolResult(call_id="", output={"exists": False}, success=False, error=f"File not found: {p}")

    registry.register_tool(
        ToolDescriptor("check_file", "verification", WORKER_AND_ABOVE, "Check if a file exists and get its size.",
                       {"path": {"type": "string"}}), check)
