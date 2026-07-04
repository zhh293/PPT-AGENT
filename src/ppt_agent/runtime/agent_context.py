"""Dual-layer context isolation — TeammateContext + AgentContext.

Resolution priority (highest to lowest):
1. Agent-local ContextVar (set by agent_attribution context manager)
2. Dynamic team context (set by team lead or coordinator)
3. Environment variables (PPT_AGENT_*)

TeammateContext holds team-level shared state (job workspace, event bus, etc.)
AgentContext holds agent-specific state (current phase, tool history, etc.)
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ─── Backward-compatible agent attribution ──────────────────────────────

current_agent: ContextVar[str] = ContextVar("current_agent", default="unknown")


@contextmanager
def agent_attribution(agent: str):
    token = current_agent.set(agent)
    try:
        yield
    finally:
        current_agent.reset(token)


# ─── Team-level context ─────────────────────────────────────────────────

@dataclass
class TeammateContext:
    """Team-level shared state visible to all agents in a job."""

    workspace_root: Path | None = None
    event_bus: Any | None = None
    skill_loader: Any | None = None
    job_id: str = ""
    team_role: str = "worker"


_teammate_ctx: ContextVar[TeammateContext | None] = ContextVar(
    "_teammate_ctx", default=None
)


def set_teammate_context(ctx: TeammateContext):
    """Set the teammate context. Returns a token for resetting."""
    return _teammate_ctx.set(ctx)


def get_teammate_context() -> TeammateContext | None:
    """Return the current teammate context, or None if unset."""
    return _teammate_ctx.get()


# ─── Agent-level context ────────────────────────────────────────────────

@dataclass
class AgentContext:
    """Agent-specific state for the currently executing agent."""

    agent_id: str = ""
    phase: str = ""
    tool_history: list = field(default_factory=list)
    turn_count: int = 0


_agent_ctx: ContextVar[AgentContext | None] = ContextVar(
    "_agent_ctx", default=None
)


def set_agent_context(ctx: AgentContext):
    """Set the agent context. Returns a token for resetting."""
    return _agent_ctx.set(ctx)


def get_agent_context() -> AgentContext | None:
    """Return the current agent context, or None if unset."""
    return _agent_ctx.get()


# ─── Resolution function ────────────────────────────────────────────────

def resolve(key: str) -> Any:
    """Resolve a configuration key through the context hierarchy.

    Resolution order:
    1. Agent context — check attribute by *key* on the current AgentContext.
    2. Teammate context — check attribute by *key* on the current TeammateContext.
    3. Environment variable — ``PPT_AGENT_<KEY>`` (uppercased).

    Returns ``None`` if the key is not found at any level.
    """
    # 1. Agent-local context
    agent = _agent_ctx.get()
    if agent is not None:
        val = getattr(agent, key, None)
        if val is not None:
            return val

    # 2. Teammate / team context
    team = _teammate_ctx.get()
    if team is not None:
        val = getattr(team, key, None)
        if val is not None:
            return val

    # 3. Environment variables
    env_key = f"PPT_AGENT_{key.upper()}"
    env_val = os.environ.get(env_key)
    if env_val is not None:
        return env_val

    return None


# ─── Scope context managers ─────────────────────────────────────────────

@contextmanager
def teammate_scope(ctx: TeammateContext):
    """Context manager that sets and resets TeammateContext."""
    token = _teammate_ctx.set(ctx)
    try:
        yield ctx
    finally:
        _teammate_ctx.reset(token)


@contextmanager
def agent_scope(ctx: AgentContext):
    """Context manager that sets and resets AgentContext."""
    token = _agent_ctx.set(ctx)
    try:
        yield ctx
    finally:
        _agent_ctx.reset(token)
