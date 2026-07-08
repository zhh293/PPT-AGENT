"""template_matching tools — search_templates."""

from ppt_agent.retrieval.query_router import query
from ppt_agent.retrieval.template_index import load_template_index
from ppt_agent.runtime.agent_loop import ToolResult
from ppt_agent.tools.registry import WORKER_AND_ABOVE, ToolDescriptor


def register_tools(registry, workspace, capability, llm_client=None):
    def executor(args: dict) -> ToolResult:
        q = args.get("query", "")
        top_k = int(args.get("top_k", 3))
        try:
            templates = load_template_index()
            items = [{"id": t["template_id"], "text": t.get("retrieval_text", ""), **t} for t in templates]
            ranked = query(items, q, ["bm25", "vector"], top_k)
            return ToolResult(call_id="", output={"query": q, "results": ranked[:top_k]}, success=True)
        except Exception as e:
            return ToolResult(call_id="", output=None, success=False, error=str(e))

    registry.register_tool(
        ToolDescriptor("search_templates", "retrieval", WORKER_AND_ABOVE,
                       "Search the template library using hybrid retrieval.",
                       {"query": {"type": "string"}, "top_k": {"type": "integer", "default": 3}}),
        executor,
    )
