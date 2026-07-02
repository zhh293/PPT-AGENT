from __future__ import annotations

from contextvars import ContextVar
from contextlib import contextmanager

current_agent: ContextVar[str] = ContextVar("current_agent", default="unknown")


@contextmanager
def agent_attribution(agent: str):
    token = current_agent.set(agent)
    try:
        yield
    finally:
        current_agent.reset(token)
