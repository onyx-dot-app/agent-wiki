"""FastAPI app entry — wires routers, middleware, exception handlers,
and the lifespan that owns process-wide startup.

Production launch: ``uvicorn --factory app.main:create_app`` (see
``backend/Dockerfile`` and ``.vscode/launch.json``).

Tests build the same app via ``TestClient(create_app())``; the
lifespan only fires when ``TestClient`` is used as a context manager,
so per-test fixtures (``tmp_db`` / ``tmp_repo``) keep owning DB and
wiki setup.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from app.api import (
    admin,
    auth,
    chat,
    documents,
    events,
    health,
    llm,
    mcp_connections,
    mcp_server,
    mcp_tokens,
    permissions,
    templates,
    triggers,
    user,
    users,
    webhooks,
)
from app.auth import PermissionDenied
from app.auth.deps import CurrentUserMiddleware
import app.config as _app_config
from app.mcp_server import pubsub as mcp_pubsub
from app.models._helpers import ErrorResponse, QueueFullErrorResponse, RequestError
from app.tasks.queues import QueueFullError

log = logging.getLogger(__name__)


def _on_http_exception(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, FastAPIHTTPException)
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(error=str(exc.detail)).model_dump(),
    )


def _on_validation_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return JSONResponse(
        status_code=400,
        content=ErrorResponse(error=str(exc)).model_dump(),
    )


def _on_permission_denied(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, PermissionDenied)
    return JSONResponse(
        status_code=403,
        content=ErrorResponse(error=exc.message).model_dump(),
    )


def _on_queue_full(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, QueueFullError)
    return JSONResponse(
        status_code=503,
        content=QueueFullErrorResponse(
            error=str(exc),
            queue=exc.queue_name,
            size=exc.size,
            limit=exc.limit,
        ).model_dump(),
    )


def _on_request_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestError)
    return JSONResponse(
        status_code=exc.status,
        content=ErrorResponse(error=exc.message).model_dump(),
    )


def _install_error_handlers(app: FastAPI) -> None:
    """Translate domain exceptions into the standard
    ``{"error": "..."}`` envelope the frontend's ``ApiError`` parses."""
    app.add_exception_handler(FastAPIHTTPException, _on_http_exception)
    app.add_exception_handler(RequestValidationError, _on_validation_error)
    app.add_exception_handler(PermissionDenied, _on_permission_denied)
    app.add_exception_handler(QueueFullError, _on_queue_full)
    app.add_exception_handler(RequestError, _on_request_error)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Run shared backend startup at uvicorn boot.

    Tests build the bare app via ``create_app()`` + ``TestClient(app)``
    without entering the context manager, so the lifespan body is
    skipped — fixtures (``tmp_db`` / ``tmp_repo``) own DB/wiki init.
    """
    from app.db.session import init_db
    from app.tasks.agent_activity import schedule_all_pending_cleanups
    from app.triggers import repo as triggers_repo
    from app.utils.logging import setup_logging
    from app.wiki.git import ensure_wiki_repo
    from app.wiki.seed import seed_if_empty
    from app.wiki.templates import seed_starter_templates_if_empty

    setup_logging()
    log.info("agent-wiki backend starting (database=%s)", _app_config.CONFIG.database_url.split("@")[-1])
    init_db()
    ensure_wiki_repo()
    # Seed-on-empty runs after the repo is initialized so writes go
    # through the normal commit + notify path (FTS index, ACLs, MCP
    # fan-out all fire identically to a UI save).
    seed_if_empty(_app_config.CONFIG.wiki_dir)
    # Starter document templates seed once on a brand-new DB; users
    # who delete a starter will not see it re-appear after a reboot.
    seed_starter_templates_if_empty()
    triggers_repo.purge_invalid_triggers(actor="system <system@agent-wiki>")
    triggers_repo.rebuild_from_filesystem()
    schedule_all_pending_cleanups()
    # Cross-process MCP pub-sub bridge: the worker process commits docs,
    # the web process owns the SSE stream — Postgres LISTEN/NOTIFY
    # ferries update events between them.
    mcp_pubsub.start_listener()

    yield

    mcp_pubsub.stop_listener()


def create_app() -> FastAPI:
    """Build the agent-wiki FastAPI app."""
    app = FastAPI(
        title="agent-wiki",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )
    # Order matters here: ``add_middleware`` builds a stack where the
    # last added is the outermost / runs first on inbound. We want
    # ``SessionMiddleware`` to be outermost so ``request.session`` is
    # populated by the time ``CurrentUserMiddleware`` reads from it.
    app.add_middleware(CurrentUserMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=_app_config.CONFIG.secret_key,
        session_cookie="session",
        same_site="lax",
        https_only=_app_config.CONFIG.secure_cookies,
        max_age=30 * 24 * 3600,
    )
    _install_error_handlers(app)
    app.include_router(health.router, prefix="/api/health")
    app.include_router(llm.router, prefix="/api/llm")
    app.include_router(events.router, prefix="/api/events")
    app.include_router(user.router, prefix="/api/user")
    app.include_router(users.router, prefix="/api/users")
    app.include_router(mcp_connections.router, prefix="/api/mcp/connections")
    app.include_router(mcp_tokens.router, prefix="/api/mcp/tokens")
    app.include_router(webhooks.router, prefix="/api/webhooks")
    app.include_router(admin.router, prefix="/api/admin")
    app.include_router(templates.admin_router, prefix="/api/admin/templates")
    app.include_router(templates.router, prefix="/api/templates")
    app.include_router(permissions.router, prefix="/api")
    app.include_router(triggers.router, prefix="/api/triggers")
    app.include_router(documents.router, prefix="/api/documents")
    app.include_router(chat.router, prefix="/api/chat")
    app.include_router(mcp_server.router, prefix="/api/mcp")
    app.include_router(auth.router, prefix="/api/auth")
    return app
