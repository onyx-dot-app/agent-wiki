"""HTTP API for ``agent_sessions``.

Routes mounted under ``/api/agent-sessions``. All routes accept either
session-cookie user (browser-driven) OR MCP bearer (helper-driven) —
the helper runs without a cookie (AF#2 audit fix).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.auth import User
from app.auth.deps import require_user_or_bearer
from app.config import CONFIG
from app.launchers import sessions as sessions_repo
from app.models.launchers import (
    AgentSessionList,
    AgentSessionSummary,
    CliSessionUpdateRequest,
    CloseRequest,
)

router = APIRouter()


# AF#11 — error reasons that should mark the session ``failed`` rather
# than ``closed``.
_ERROR_REASONS = frozenset(
    {
        "cli_not_found",
        "invalid_workdir",
        "spawn_failed",
        "binary_not_allowed",
        "manifest_version_unsupported",
        "endpoint_mismatch",
    }
)


def _check_flag() -> None:
    if not CONFIG.launchers_enabled:
        raise HTTPException(status_code=404, detail="launchers disabled")


def _require_own_session(sid: str, user: User) -> dict[str, object]:
    row = sessions_repo.get(sid)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    if row["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="not your session")
    return row


@router.get("", response_model=AgentSessionList)
def list_sessions(
    wiki_path: str | None = None,
    user: User = Depends(require_user_or_bearer),
) -> AgentSessionList:
    _check_flag()
    if wiki_path is not None:
        rows = sessions_repo.list_for_page(user_id=user.id, wiki_path=wiki_path)
    else:
        rows = sessions_repo.list_for_user(user.id)
    return AgentSessionList(
        sessions=[
            AgentSessionSummary(
                id=r["id"],
                tool_id=r["tool_id"],
                wiki_path=r["wiki_path"],
                working_dir=r["working_dir"],
                status=r["status"],
                started_at=r["started_at"],
                last_activity_at=r["last_activity_at"],
                closed_at=r["closed_at"],
                cli_session_id=r["cli_session_id"],
            )
            for r in rows
        ]
    )


@router.post("/{sid}/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
def heartbeat(sid: str, user: User = Depends(require_user_or_bearer)) -> Response:
    _check_flag()
    _require_own_session(sid, user)
    sessions_repo.touch_activity(sid)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{sid}/cli-session", status_code=status.HTTP_204_NO_CONTENT)
def set_cli_session(
    sid: str,
    req: CliSessionUpdateRequest,
    user: User = Depends(require_user_or_bearer),
) -> Response:
    _check_flag()
    _require_own_session(sid, user)
    sessions_repo.set_cli_session_id(sid, req.cli_session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{sid}/spawn-ok", status_code=status.HTTP_204_NO_CONTENT)
def spawn_ok(sid: str, user: User = Depends(require_user_or_bearer)) -> Response:
    """R9#1 — helper POSTs after handing the spawn to Terminal.app.

    Sweep watches for this; if absent within 30s, session flips
    ``failed`` so the UI stops showing stale ``active`` sessions.
    """
    _check_flag()
    _require_own_session(sid, user)
    sessions_repo.mark_spawn_ok(sid)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{sid}/close", status_code=status.HTTP_204_NO_CONTENT)
def close_session(
    sid: str,
    req: CloseRequest,
    user: User = Depends(require_user_or_bearer),
) -> Response:
    """AF#11 — error reasons mark the session ``failed`` rather than ``closed``."""
    _check_flag()
    _require_own_session(sid, user)
    reason = req.reason or "user_clicked"
    if reason in _ERROR_REASONS:
        sessions_repo.mark_failed(sid, reason=reason)
    else:
        sessions_repo.close(sid, reason=reason)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
