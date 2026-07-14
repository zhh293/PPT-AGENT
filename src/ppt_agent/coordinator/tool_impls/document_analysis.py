"""document_analysis tools — read_input_files."""

from ppt_agent.runtime.agent_loop import ToolResult
from ppt_agent.tools.registry import WORKER_AND_ABOVE, ToolDescriptor
from ppt_agent.tools.documents import extract_text


def register_tools(registry, workspace, capability, llm_client=None):
    def executor(call_id: str, arguments: dict) -> ToolResult:
        max_chars = int(arguments.get("max_chars", 10000))
        try:
            files = sorted(workspace.input_dir.rglob("*"))
            texts = []
            for f in files:
                if f.is_file():
                    t = extract_text(f)
                    if t:
                        texts.append(f"[{f.name}]\n{t[:max_chars]}")
            return ToolResult(call_id=call_id, output={"files": len(files), "text": "\n\n".join(texts)}, success=True)
        except Exception as e:
            return ToolResult(call_id=call_id, output=None, success=False, error=str(e))

    registry.register_tool(
        ToolDescriptor("read_input_files", "extraction", WORKER_AND_ABOVE,
                       "Read raw input files from the workspace input directory.",
                       {"max_chars": {"type": "integer", "default": 10000}}),
        executor,
    )
