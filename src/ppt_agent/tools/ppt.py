from __future__ import annotations

from pathlib import Path

from ppt_agent.assembly.ppt_writer import write_pptx


def assemble_pptx(slide_contents: dict, output_path: Path) -> Path:
    write_pptx(slide_contents, output_path)
    return output_path
