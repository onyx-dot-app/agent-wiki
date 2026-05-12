"""ContextVar for the launcher agent-session id active on the current request.

The MCP server middleware reads the ``X-Agentwiki-Session`` header and
binds the id here; the wiki commit pipeline reads it to stamp
``agent_activity.agent_session_id``.

Mirrors ``app.auth.current_user_ctx``.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

current_agent_session_id_ctx: ContextVar[str | None] = ContextVar(
    "current_agent_session_id",
    default=None,
)


def current_agent_session_id() -> str | None:
    return current_agent_session_id_ctx.get()


@contextmanager
def set_current_agent_session_id(sid: str | None) -> Iterator[None]:
    token = current_agent_session_id_ctx.set(sid)
    try:
        yield
    finally:
        current_agent_session_id_ctx.reset(token)
