"""Unified tool registry with RBAC permission enforcement.

Registers all 9 tool categories with role-based access control.
Four roles: main_agent, coordinator, worker, sub_agent.

AGENT_ARCHITECTURE.md §5:
  Three-layer permission check:
    1. Registration check — is the tool in the registry?
    2. Category check    — does the caller role have access to this category?
    3. Tool-level check  — does the tool descriptor allow this role?

Optional fourth layer: caller's explicit allowlist (maintained per-agent,
not enforced at the registry level).

Thread safety: The registry is designed for populate-then-use.  All tools
should be registered before any execution begins.  Dynamic registration
during active use is not supported.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from typing import Any

from ppt_agent.runtime.agent_loop import ToolCall, ToolResult

logger = logging.getLogger(__name__)


# ─── Tool Executor Protocol ─────────────────────────────────────────────
class ToolExecutor:
    """Protocol for tool execution functions.

    Every registered tool must provide a callable matching this signature.
    The *call_id* is passed so the executor can thread traceability through
    to the result.
    """

    def __call__(self, call_id: str, arguments: dict[str, Any]) -> ToolResult: ...


# ─── Tool Descriptor ────────────────────────────────────────────────────
@dataclass(frozen=True)
class ToolDescriptor:
    """Descriptor for a registered tool.

    ``frozen=True`` prevents field reassignment but does NOT prevent
    mutation of mutable fields such as ``parameters``.  Treat
    ``parameters`` as read-only once constructed.
    """

    name: str
    category: str
    roles: frozenset[str]
    description: str = ""
    parameters: dict = field(default_factory=dict)

    def allows_role(self, role: str) -> bool:
        return role in self.roles

    def to_llm_dict(self) -> dict:
        """Convert to LLM-readable tool description dict."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


# ─── Tool Entry (Descriptor + Executor) ─────────────────────────────────
@dataclass(frozen=True)
class ToolEntry:
    """A registered tool with its executor.

    Transparently proxies all ToolDescriptor attributes so that existing
    code can use a ToolEntry wherever a ToolDescriptor was expected.
    """

    descriptor: ToolDescriptor
    executor: ToolExecutor

    # ── Transparent proxy for EVERY ToolDescriptor attribute ──────
    @property
    def name(self) -> str:
        return self.descriptor.name

    @property
    def category(self) -> str:
        return self.descriptor.category

    @property
    def roles(self) -> frozenset[str]:
        return self.descriptor.roles

    @property
    def description(self) -> str:
        return self.descriptor.description

    @property
    def parameters(self) -> dict:
        return self.descriptor.parameters

    def allows_role(self, role: str) -> bool:
        return self.descriptor.allows_role(role)

    def to_llm_dict(self) -> dict:
        return self.descriptor.to_llm_dict()


# ─── Role Definitions ──────────────────────────────────────────────────
ROLE_MAIN_AGENT = "main_agent"
ROLE_COORDINATOR = "coordinator"
ROLE_WORKER = "worker"
ROLE_SUB_AGENT = "sub_agent"

ALL_ROLES = frozenset({ROLE_MAIN_AGENT, ROLE_COORDINATOR, ROLE_WORKER, ROLE_SUB_AGENT})
WORKER_AND_ABOVE = frozenset({ROLE_MAIN_AGENT, ROLE_WORKER, ROLE_SUB_AGENT})
COORDINATOR_ONLY = frozenset({ROLE_COORDINATOR})
FULL_ACCESS = ALL_ROLES


# ─── Category-based Permissions (§5.2) ──────────────────────────────────
CATEGORY_PERMISSIONS: dict[str, frozenset[str]] = {
    "filesystem":     WORKER_AND_ABOVE,
    "execution":      WORKER_AND_ABOVE,
    "retrieval":      WORKER_AND_ABOVE,
    "assembly":       WORKER_AND_ABOVE,
    "verification":   WORKER_AND_ABOVE,
    "extraction":     WORKER_AND_ABOVE,
    "generation":     WORKER_AND_ABOVE,
    "skill":          WORKER_AND_ABOVE,
    "artifact":       FULL_ACCESS,
    "orchestration":  COORDINATOR_ONLY,
    "communication":  FULL_ACCESS,
}


def _validate_category(category: str) -> None:
    """Warn if *category* is not a known key in CATEGORY_PERMISSIONS."""
    if category not in CATEGORY_PERMISSIONS:
        warnings.warn(
            f"Category '{category}' is not in CATEGORY_PERMISSIONS. "
            f"Tools in this category will be inaccessible to all roles.",
            stacklevel=3,
        )


# ─── Tool Registry ─────────────────────────────────────────────────────
class ToolRegistry:
    """Central tool registry with executable entries and three-layer RBAC.

    Three-layer permission check (§5.3):
        1. Registration check: tool_name in registry?
        2. Category check:    role in CATEGORY_PERMISSIONS[category]?
        3. Tool-level check:  role in descriptor.roles?

    Thread safety: populate before use.  All tools should be registered
    before any calls to ``execute()``, ``check_permission()``, or the
    listing methods.

    Backward-compatible: the existing ``register(descriptor)`` and
    ``check_permission(tool_name, role)`` signatures are preserved.
    """

    def __init__(self) -> None:
        self._entries: dict[str, ToolEntry] = {}

    # ── Registration ────────────────────────────────────────────────

    def register(self, descriptor: ToolDescriptor) -> None:
        """Register a descriptor-only tool (backward-compatible).

        Tools registered this way have no executor — ``execute()`` will
        fail with a descriptive error.  Use ``register_tool()`` for
        executable tools.
        """
        if descriptor.name in self._entries:
            raise ValueError(f"Tool '{descriptor.name}' already registered")
        _validate_category(descriptor.category)
        self._entries[descriptor.name] = ToolEntry(
            descriptor=descriptor,
            executor=self._no_executor,
        )

    def register_tool(
        self,
        descriptor: ToolDescriptor,
        executor: ToolExecutor,
    ) -> None:
        """Register an executable tool with descriptor + executor.

        Args:
            descriptor: Tool metadata (name, category, roles, …).
            executor: Callable ``(call_id, arguments) -> ToolResult``.

        Raises:
            ValueError: If a tool with the same name is already registered.
        """
        if descriptor.name in self._entries:
            raise ValueError(f"Tool '{descriptor.name}' already registered")
        _validate_category(descriptor.category)
        self._entries[descriptor.name] = ToolEntry(
            descriptor=descriptor,
            executor=executor,
        )

    def register_tool_simple(
        self,
        name: str,
        category: str,
        roles: frozenset[str],
        executor: ToolExecutor,
        description: str = "",
        parameters: dict | None = None,
    ) -> None:
        """Convenience: register without manually constructing ToolDescriptor."""
        desc = ToolDescriptor(
            name=name,
            category=category,
            roles=roles,
            description=description,
            parameters=parameters or {},
        )
        self.register_tool(desc, executor)

    @staticmethod
    def _no_executor(call_id: str, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(
            call_id=call_id,
            output=None,
            success=False,
            error="Tool has no executor (descriptor-only registration). Use register_tool().",
        )

    # ── Lookup ──────────────────────────────────────────────────────

    def get(self, name: str) -> ToolEntry:
        """Get a tool entry by name.

        Raises:
            KeyError: If the tool is not registered.
        """
        if name not in self._entries:
            raise KeyError(
                f"Tool '{name}' not registered. Available: {list(self._entries.keys())}"
            )
        return self._entries[name]

    def get_descriptor(self, name: str) -> ToolDescriptor:
        """Get the ToolDescriptor for a registered tool.

        Raises:
            KeyError: If the tool is not registered.
        """
        return self.get(name).descriptor

    # ── Permission & Execution ──────────────────────────────────────

    def check_permission(self, tool_name: str, role: str) -> None:
        """Three-layer permission check (§5.3).

        Layer 1: Tool registered?
        Layer 2: Role allowed for this category?
        Layer 3: Tool descriptor allows this role?

        Raises:
            KeyError: Tool not registered (layer 1).
            PermissionError: Role not authorised (layer 2 or 3).
        """
        # Layer 1 — registration
        entry = self.get(tool_name)

        # Layer 2 — category gating
        category = entry.category
        allowed_roles = CATEGORY_PERMISSIONS.get(category, frozenset())
        if role not in allowed_roles:
            raise PermissionError(
                f"Role '{role}' has no access to category '{category}'."
            )

        # Layer 3 — tool-level role check
        if not entry.allows_role(role):
            raise PermissionError(
                f"Role '{role}' cannot use tool '{tool_name}'."
            )

    def execute(self, tool_call: ToolCall, role: str) -> ToolResult:
        """Check permissions and execute a tool call.

        Args:
            tool_call: Contains ``tool_name`` and ``arguments``.
            role: Caller's role string.

        Returns:
            ``ToolResult``.  Permission failures return a failed
            ``ToolResult`` (never raise).
        """
        try:
            self.check_permission(tool_call.tool_name, role)
        except (PermissionError, KeyError) as exc:
            return ToolResult(
                call_id=tool_call.call_id,
                output=None,
                success=False,
                error=str(exc),
            )
        entry = self.get(tool_call.tool_name)
        result = entry.executor(tool_call.call_id, tool_call.arguments)
        # Ensure call_id is always consistent
        if result.call_id != tool_call.call_id:
            result.call_id = tool_call.call_id
        return result

    # ── Query ───────────────────────────────────────────────────────

    def get_tools_for_role(self, role: str) -> list[ToolDescriptor]:
        """Return tool descriptors accessible to *role* (Layer 2 + Layer 3)."""
        result: list[ToolDescriptor] = []
        for entry in self._entries.values():
            cat_roles = CATEGORY_PERMISSIONS.get(entry.category, frozenset())
            if role in cat_roles and entry.allows_role(role):
                result.append(entry.descriptor)
        return result

    def get_tools_for_role_as_dicts(self, role: str) -> list[dict]:
        """Return LLM-ready tool description dicts for *role*."""
        return [d.to_llm_dict() for d in self.get_tools_for_role(role)]

    def list_tools(self, role: str | None = None) -> list[ToolDescriptor]:
        """List all tool descriptors, optionally filtered by role.

        When *role* is given, filtering applies both category gating
        (CATEGORY_PERMISSIONS) and tool-level role check, consistent
        with ``check_permission`` and ``execute``.
        """
        if role is None:
            return [e.descriptor for e in self._entries.values()]
        return self.get_tools_for_role(role)

    def list_by_category(self, category: str) -> list[ToolEntry]:
        """List all tool entries in a given category."""
        return [e for e in self._entries.values() if e.category == category]

    @property
    def tool_count(self) -> int:
        return len(self._entries)


# ─── Default Tool Definitions ──────────────────────────────────────────
# Based on AGENT_ARCHITECTURE.md Section 5: Tool Categories

_DEFAULT_TOOLS: list[ToolDescriptor] = [
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
    # Coordinator-only tools (§9)
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
