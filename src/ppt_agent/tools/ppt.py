from __future__ import annotations

from pathlib import Path

from ppt_agent.assembly.ppt_writer import write_pptx


def assemble_pptx(
    slide_contents: dict,
    output_path: Path,
    workspace_root: Path | None = None,
) -> Path:
    write_pptx(slide_contents, output_path, workspace_root=workspace_root)
    return output_path
