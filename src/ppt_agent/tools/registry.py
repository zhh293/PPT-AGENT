"""Unified tool registry with RBAC permission enforcement.

Registers all 9 tool categories with role-based access control.
Four roles: main_agent, coordinator, worker, sub_agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolDescriptor:
    """Descriptor for a registered tool."""

    name: str
    category: str
    roles: frozenset[str]
    description: str = ""

    def allows_role(self, role: str) -> bool:
        return role in self.roles


# ─── Role Definitions ──────────────────────────────────────────────────
ROLE_MAIN_AGENT = "main_agent"
ROLE_COORDINATOR = "coordinator"
ROLE_WORKER = "worker"
ROLE_SUB_AGENT = "sub_agent"

ALL_ROLES = frozenset({ROLE_MAIN_AGENT, ROLE_COORDINATOR, ROLE_WORKER, ROLE_SUB_AGENT})
WORKER_AND_ABOVE = frozenset({ROLE_MAIN_AGENT, ROLE_WORKER, ROLE_SUB_AGENT})
COORDINATOR_ONLY = frozenset({ROLE_COORDINATOR})
FULL_ACCESS = ALL_ROLES


# ─── Tool Registry ─────────────────────────────────────────────────────
class ToolRegistry:
    """Central tool registry with permission enforcement."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDescriptor] = {}

    def register(self, descriptor: ToolDescriptor) -> None:
        self._tools[descriptor.name] = descriptor

    def get(self, name: str) -> ToolDescriptor:
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not registered. Available: {list(self._tools.keys())}")
        return self._tools[name]

    def check_permission(self, tool_name: str, role: str) -> None:
        """Raise PermissionError if the role cannot use this tool."""
        descriptor = self.get(tool_name)
        if not descriptor.allows_role(role):
            raise PermissionError(
                f"Role '{role}' cannot use tool '{tool_name}'. "
                f"Allowed roles: {descriptor.roles}"
            )

    def list_tools(self, role: str | None = None) -> list[ToolDescriptor]:
        """List tools, optionally filtered by role."""
        if role is None:
            return list(self._tools.values())
        return [t for t in self._tools.values() if t.allows_role(role)]

    @property
    def tool_count(self) -> int:
        return len(self._tools)


# ─── Default Tool Definitions ──────────────────────────────────────────
# Based on AGENT_ARCHITECTURE.md Section 5: Tool Categories

_DEFAULT_TOOLS = [
    ToolDescriptor(
        name="FileSystemTool",
        category="filesystem",
        roles=WORKER_AND_ABOVE,
        description="Read/write files within the job workspace sandbox.",
    ),
    ToolDescriptor(
        name="JsonArtifactTool",
        category="artifact",
        roles=WORKER_AND_ABOVE,
        description="Read/write JSON artifact files (source_summary, outline, etc.).",
    ),
    ToolDescriptor(
        name="DocumentExtractionTool",
        category="extraction",
        roles=WORKER_AND_ABOVE,
        description="Extract text from .txt, .md, .docx, .csv, .xlsx files.",
    ),
    ToolDescriptor(
        name="OCRTool",
        category="extraction",
        roles=WORKER_AND_ABOVE,
        description="OCR processing for image-based documents.",
    ),
    ToolDescriptor(
        name="RetrievalTool",
        category="retrieval",
        roles=WORKER_AND_ABOVE,
        description="BM25/vector retrieval against knowledge bases.",
    ),
    ToolDescriptor(
        name="SkillScriptTool",
        category="skill",
        roles=WORKER_AND_ABOVE,
        description="Execute skill-provided scripts (gptimage2_client.py, etc.).",
    ),
    ToolDescriptor(
        name="ImageGenerationTool",
        category="generation",
        roles=WORKER_AND_ABOVE,
        description="Generate images via gptimage2 or other providers.",
    ),
    ToolDescriptor(
        name="PPTAssemblyTool",
        category="assembly",
        roles=WORKER_AND_ABOVE,
        description="Assemble final .pptx from slide contents.",
    ),
    ToolDescriptor(
        name="RenderVerifyTool",
        category="verification",
        roles=WORKER_AND_ABOVE,
        description="Render and verify the generated PPT.",
    ),
    # Coordinator-only tools (Section 9)
    ToolDescriptor(
        name="AgentTool",
        category="orchestration",
        roles=COORDINATOR_ONLY,
        description="Spawn sub-agents for delegated tasks.",
    ),
    ToolDescriptor(
        name="TaskStopTool",
        category="orchestration",
        roles=COORDINATOR_ONLY,
        description="Stop a running sub-agent task.",
    ),
    ToolDescriptor(
        name="SendMessageTool",
        category="orchestration",
        roles=COORDINATOR_ONLY,
        description="Send messages between agents via mailbox.",
    ),
    ToolDescriptor(
        name="SyntheticOutput",
        category="orchestration",
        roles=COORDINATOR_ONLY,
        description="Produce synthesized output from sub-agent results.",
    ),
]


def create_default_registry() -> ToolRegistry:
    """Create a registry pre-populated with all default tools."""
    registry = ToolRegistry()
    for tool in _DEFAULT_TOOLS:
        registry.register(tool)
    return registry
