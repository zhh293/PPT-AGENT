"""ppt_assembly tools — support both agent-driven and deterministic assembly."""

import json
from pathlib import Path

from ppt_agent.runtime.agent_loop import ToolResult
from ppt_agent.tools.registry import WORKER_AND_ABOVE, ToolDescriptor


def register_tools(registry, workspace, capability, llm_client=None):
    def assemble(call_id: str, arguments: dict) -> ToolResult:
        """Assemble the final PPTX.

        Two paths:

        1. **Agent-driven** (when *mappings* is provided): The Agent has read
           template_zones + slide_contents and decided which zone_id gets which
           content.  We route to ``assemble_with_mapping()``.

        2. **Deterministic fallback** (no mappings): Runs the original
           ``ppt_assembler.run()`` — heuristic type-inference + reading-order
           matching.
        """
        try:
            mappings_raw = arguments.get("mappings")
            image_mappings_raw = arguments.get("image_mappings", "{}")
            force = arguments.get("force", False)

            if mappings_raw is not None:
                # ── Agent-driven path ──
                mappings = (
                    json.loads(mappings_raw)
                    if isinstance(mappings_raw, str)
                    else mappings_raw
                )
                image_mappings = (
                    json.loads(image_mappings_raw)
                    if isinstance(image_mappings_raw, str)
                    else image_mappings_raw
                )

                from ppt_agent.workers.ppt_assembler import assemble_with_mapping
                output = assemble_with_mapping(
                    workspace, mappings, image_mappings, force,
                )
                return ToolResult(
                    call_id=call_id,
                    output={"path": str(output), "method": "agent_driven"},
                    success=True,
                )
            else:
                # ── Deterministic fallback ──
                from ppt_agent.workers.ppt_assembler import run as assemble_run
                output = assemble_run(workspace, force=force)
                return ToolResult(
                    call_id=call_id,
                    output={"path": str(output), "method": "deterministic"},
                    success=True,
                )
        except Exception as e:
            return ToolResult(
                call_id=call_id, output=None, success=False, error=str(e),
            )

    registry.register_tool(
        ToolDescriptor(
            "assemble_pptx", "assembly", WORKER_AND_ABOVE,
            "Assemble the final .pptx file.\n\n"
            "AGENT-DRIVEN MODE: Read template_zones and slide_contents first, "
            "then call with mappings to specify exactly which zone_id gets which "
            "content.\n\n"
            "mappings: {slide_index: {content_type: {zone_id, content}}}\n"
            "  - content_type: 'title', 'subtitle', 'bullets', 'body'\n"
            "  - zone_id: MUST come from template_zones.all_zones[].zone_id\n"
            "  - content: string (title/subtitle) or list of strings (bullets/body)\n"
            "image_mappings: {slide_index: {zone_id: image_path}}\n\n"
            "DETERMINISTIC MODE: Omit mappings to use automatic type-inference "
            "matching.",
            {
                "mappings": {
                    "type": "string",
                    "description": "JSON string: {slide_index: {content_type: {zone_id, content}}}",
                },
                "image_mappings": {
                    "type": "string",
                    "default": "{}",
                    "description": "JSON string: {slide_index: {zone_id: image_path}}",
                },
                "force": {"type": "boolean", "default": False},
            },
        ),
        assemble,
    )

    def check(call_id: str, arguments: dict) -> ToolResult:
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
            output={"exists": False},
            success=False,
            error=f"File not found: {p}",
        )

    registry.register_tool(
        ToolDescriptor(
            "check_file", "verification", WORKER_AND_ABOVE,
            "Check if a file exists and get its size.",
            {"path": {"type": "string"}},
        ),
        check,
    )
