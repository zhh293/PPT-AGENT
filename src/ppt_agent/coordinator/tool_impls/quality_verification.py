"""quality_verification tools — run_verification, check_file, check_file_exists."""

from pathlib import Path

from ppt_agent.runtime.agent_loop import ToolResult
from ppt_agent.tools.registry import WORKER_AND_ABOVE, ToolDescriptor


def register_tools(registry, workspace, capability, llm_client=None):
    # ── run_verification ──────────────────────────────────────────────
    def run_verification(call_id: str, arguments: dict) -> ToolResult:
        """Run verification — structural checks + visual audit + agent evaluates results."""
        try:
            from ppt_agent.workers.ppt_verifier import run as verify_run
            force = arguments.get("force", False)
            output = verify_run(workspace, force=force)  # No llm_client — agent does the review
            return ToolResult(call_id=call_id, output={"path": str(output), "status": "ok"}, success=True)
        except Exception as e:
            return ToolResult(call_id=call_id, output=None, success=False, error=str(e))

    registry.register_tool(
        ToolDescriptor("run_verification", "verification", WORKER_AND_ABOVE,
                       "Run quality verification on the final PPTX (content completeness, visual audit, text accuracy).",
                       {"force": {"type": "boolean", "default": False}}),
        run_verification)

    # ── check_file (same semantics as ppt_assembly's check_file) ─────
    def check_file(call_id: str, arguments: dict) -> ToolResult:
        """Check if a file exists and return its size.  Fails when the file is missing."""
        p = Path(arguments.get("path", ""))
        if not p.is_absolute():
            p = workspace.root / p
        if p.exists():
            return ToolResult(
                call_id=call_id,
                output={"exists": True, "path": str(p), "size": p.stat().st_size},
                success=True,
            )
        return ToolResult(
            call_id=call_id,
            output={"exists": False, "path": str(p)},
            success=False,
            error=f"File not found: {p}",
        )

    registry.register_tool(
        ToolDescriptor(
            "check_file", "verification", WORKER_AND_ABOVE,
            "Check if a file exists and get its size. Returns error if the file is missing.",
            {"path": {"type": "string", "description": "File path (absolute or relative to workspace)"}},
        ),
        check_file,
    )

    # ── check_file_exists (same semantics as visual_generation's) ────
    def check_exists(call_id: str, arguments: dict) -> ToolResult:
        """Check if a file exists — always succeeds, just reports whether it was found."""
        p = Path(arguments.get("path", ""))
        if not p.is_absolute():
            p = workspace.root / p
        return ToolResult(
            call_id=call_id,
            output={"exists": p.exists(), "path": str(p)},
            success=True,
        )

    registry.register_tool(
        ToolDescriptor(
            "check_file_exists", "verification", WORKER_AND_ABOVE,
            "Check if a file or directory exists at the given path. Always succeeds, reports exists=true/false.",
            {"path": {"type": "string", "description": "Path to check (absolute or relative to workspace)"}},
        ),
        check_exists,
    )
