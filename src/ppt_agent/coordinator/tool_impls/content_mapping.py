"""content_mapping tools — map_slide_content delegates to worker."""

from ppt_agent.runtime.agent_loop import ToolResult
from ppt_agent.tools.registry import WORKER_AND_ABOVE, ToolDescriptor


def register_tools(registry, workspace, capability, llm_client=None):
    preview_attempts = 0

    def map_content(call_id: str, arguments: dict) -> ToolResult:
        """Run the LLM-first mapping pipeline with deterministic validation."""
        try:
            from ppt_agent.workers.content_mapper import run as mapper_run
            force = arguments.get("force", False)
            output = mapper_run(workspace, force=force, llm_client=llm_client)
            return ToolResult(call_id=call_id, output={"path": str(output), "status": "ok"}, success=True)
        except Exception as e:
            return ToolResult(call_id=call_id, output=None, success=False, error=str(e))

    registry.register_tool(
        ToolDescriptor("map_slide_content", "content", WORKER_AND_ABOVE,
                       "Map outline content into template zones using the full mapping pipeline (position locking, formatting injection, visual data).",
                       {"force": {"type": "boolean", "default": False}}),
        map_content)

    def preview_mapping(call_id: str, arguments: dict) -> ToolResult:
        """Assemble and audit a disposable candidate without touching final.pptx."""
        nonlocal preview_attempts
        if preview_attempts >= 2:
            return ToolResult(
                call_id=call_id,
                output={"attempts_used": preview_attempts, "max_attempts": 2},
                success=False,
                error="Mapping preview budget exhausted; resolve remaining issues at user review.",
            )
        preview_attempts += 1

        try:
            from ppt_agent.assembly.html_preview import generate_preview_html
            from ppt_agent.assembly.ppt_writer import write_pptx, write_pptx_from_mapping
            from ppt_agent.assembly.render_verify import inspect_pptx
            from ppt_agent.coordinator.phase_state import load_artifact
            from ppt_agent.vision.pptx_audit import audit_pptx
            from ppt_agent.workers.content_mapper import _validate_mapping_plan
            from ppt_agent.workers.ppt_assembler import (
                _build_mappings_from_slide_contents,
                _build_overflow_report,
                _find_template_pptx,
            )

            candidate = arguments.get("candidate")
            if not isinstance(candidate, dict):
                candidate = load_artifact(workspace, "slide_contents")
            template_zones = load_artifact(workspace, "template_zones")

            template_by_index = {
                int(slide.get("index", index)): slide
                for index, slide in enumerate(template_zones.get("slides", []))
            }
            mapping_issues: list[dict] = []
            for output_index, slide in enumerate(candidate.get("slides", [])):
                template_index = int(slide.get("template_slide_index", output_index))
                issues = _validate_mapping_plan(
                    slide, template_by_index.get(template_index, {})
                )
                if issues:
                    mapping_issues.append({
                        "slide_index": output_index,
                        "template_slide_index": template_index,
                        "issues": issues,
                    })

            preview_pptx = workspace.root / "mapping_preview.pptx"
            template_path = _find_template_pptx(workspace)
            if template_path:
                mappings, image_mappings = _build_mappings_from_slide_contents(candidate)
                write_pptx_from_mapping(
                    template_path,
                    template_zones,
                    mappings,
                    image_mappings,
                    candidate,
                    preview_pptx,
                    workspace.root,
                )
            else:
                write_pptx(candidate, preview_pptx, workspace_root=workspace.root)

            preview_html = workspace.root / "mapping_preview.html"
            preview_html.write_text(
                generate_preview_html(candidate, job_root=workspace.root),
                encoding="utf-8",
            )
            structural = inspect_pptx(
                preview_pptx, expected_slide_count=len(candidate.get("slides", []))
            )
            visual = audit_pptx(preview_pptx).to_dict()

            rendered_paths: list[str] = []
            try:
                from ppt_agent.templates.ingest import _export_slides_to_images

                render_dir = workspace.root / "mapping_preview"
                render_dir.mkdir(exist_ok=True)
                rendered = _export_slides_to_images(
                    preview_pptx,
                    render_dir,
                    len(candidate.get("slides", [])),
                )
                rendered_paths = [str(path) for path in rendered]
            except Exception:
                # HTML and the structural audit remain useful on hosts without
                # a PowerPoint renderer.
                rendered_paths = []

            overflow_zones = _build_overflow_report(candidate, template_zones)
            return ToolResult(
                call_id=call_id,
                success=True,
                output={
                    "attempt": preview_attempts,
                    "max_attempts": 2,
                    "preview_pptx": str(preview_pptx),
                    "preview_html": str(preview_html),
                    "rendered_slides": rendered_paths,
                    "mapping_issues": mapping_issues,
                    "overflow_zones": overflow_zones,
                    "structural_audit": structural,
                    "visual_audit": visual,
                    "revision_required": bool(
                        mapping_issues
                        or overflow_zones
                        or structural.get("warnings")
                        or visual.get("total_overflow_issues", 0)
                    ),
                },
            )
        except Exception as exc:
            return ToolResult(
                call_id=call_id,
                output={"attempt": preview_attempts, "max_attempts": 2},
                success=False,
                error=str(exc),
            )

    registry.register_tool(
        ToolDescriptor(
            "preview_mapping",
            "verification",
            WORKER_AND_ABOVE,
            (
                "Assemble a disposable mapping preview, render it when possible, "
                "and return mapping, overflow, structural, and visual feedback. "
                "At most two calls are allowed."
            ),
            {
                "candidate": {
                    "type": "object",
                    "description": "Complete candidate slide_contents object.",
                }
            },
        ),
        preview_mapping,
    )
