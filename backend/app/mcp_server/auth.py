"""Bearer-token middleware for the inbound MCP transport.

Resolves ``Authorization: Bearer mcp_<token>`` to the owning ``User``,
**stuffs it into ``flask.g.user``** so every downstream helper that
reads the active user (``app.auth.current_user``, ``require_can``, ACL
lifecycle hooks, agent-activity attribution, trigger ``actor`` field)
sees the right principal — and returns HTTP 401 on auth failure.

This is the single seam that lets MCP requests reuse the existing
authorization machinery unchanged. Below this point, no module needs
to know whether the active session was authenticated via cookie or
bearer token. See the "Auth" and "Authorization" sections of
``local_data/wiki/mcp-server/mcp-server.md``.

Why a separate decorator and not ``@login_required``: that decorator
reads from the Flask session cookie. MCP clients carry no cookie; they
authenticate per-request via the ``Authorization`` header.
"""
from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable, TypeVar, cast

from flask import g, jsonify, request

from app.auth import mcp_tokens as tokens_repo

log = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

_BEARER_PREFIX = "Bearer "


def bearer_required(fn: F) -> F:
    """Wrap a Flask view so it only runs after a valid bearer token has
    been resolved to ``g.user``. Returns 401 with the standard
    ``{"error": ...}`` envelope on failure.
    """

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        header = request.headers.get("Authorization", "")
        if not header.startswith(_BEARER_PREFIX):
            return jsonify(error="missing bearer token"), 401
        raw = header[len(_BEARER_PREFIX):].strip()
        user = tokens_repo.verify(raw)
        if user is None:
            log.info("mcp bearer rejected (token unrecognized)")
            return jsonify(error="invalid bearer token"), 401
        g.user = user
        return fn(*args, **kwargs)

    return cast(F, wrapper)
