"""ToolFactory — creates per-capability ToolRegistry instances.

For each AgentCapability, assembles the set of common + domain-specific
tools the worker is allowed to use.
"""

from __future__ import annotations

import importlib
import logging

from ppt_agent.coordinator.capabilities import AgentCapability
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_common_tool_names = {"read_artifact", "write_artifact", "validate_output", "search_knowledge_base", "load_skill"}


class ToolFactory:
    """Create ToolRegistry instances populated with capability-specific tools."""

    def __init__(self, workspace: JobWorkspace) -> None:
        self.workspace = workspace

    def create_registry_for_capability(
        self,
        capability: AgentCapability,
        *,
        skill_loader=None,
        llm_client=None,
    ) -> ToolRegistry:
        """Build a ToolRegistry with all tools this capability is allowed to use.

        Common tools (read/write/validate/search/load_skill) are always
        registered.  Domain-specific tools are loaded from
        ``tool_impls/<capability_id>.py`` when available.
        """
        registry = ToolRegistry()
        self._register_common(registry, capability, skill_loader)
        self._register_domain(registry, capability, llm_client)
        return registry

    def _register_common(self, registry, capability, skill_loader=None) -> None:
        from ppt_agent.coordinator.tool_impls.common import (
            make_load_skill, make_read_artifact, make_search_knowledge_base,
            make_validate_output, make_write_artifact,
        )
        makers = {
            "read_artifact": make_read_artifact,
            "write_artifact": make_write_artifact,
            "validate_output": make_validate_output,
            "search_knowledge_base": make_search_knowledge_base,
            "load_skill": make_load_skill,
        }
        for tool_name in capability.tools:
            if tool_name in makers:
                desc, executor = makers[tool_name](self.workspace, skill_loader=skill_loader)
                if tool_name == "load_skill":
                    # load_skill executor is bound by AgentLoop, not here
                    if executor is not None:
                        registry.register_tool(desc, executor)
                    else:
                        registry.register(desc)  # descriptor-only; AgentLoop wires it
                else:
                    registry.register_tool(desc, executor)

    def _register_domain(self, registry, capability, llm_client=None) -> None:
        module_name = f"ppt_agent.coordinator.tool_impls.{capability.capability_id}"
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            return

        if hasattr(module, "register_tools"):
            module.register_tools(registry, self.workspace, capability, llm_client)
