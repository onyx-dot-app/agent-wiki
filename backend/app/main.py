"""Flask app entry. Wires up blueprints, then serves on 8080."""
from __future__ import annotations

from datetime import timedelta

from flask import Flask, jsonify

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
    triggers,
    users,
    webhooks,
)
from app.auth import PermissionDenied
from app.auth.oidc import init_oauth
from app.config import CONFIG
from app.db.session import init_db
from app.mcp_server import pubsub as mcp_pubsub
from app.models._helpers import ErrorResponse, QueueFullErrorResponse, RequestError
from app.tasks.agent_activity import schedule_all_pending_cleanups
from app.tasks.queues import QueueFullError
from app.triggers import repo as triggers_repo
from app.utils.logging import setup_logging
from app.wiki.git import ensure_wiki_repo


def create_app() -> Flask:
    setup_logging()
    app = Flask(__name__)
    app.config.update(  # pyright: ignore[reportUnknownMemberType]
        SECRET_KEY=CONFIG.secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=CONFIG.secure_cookies,
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    )

    init_db()
    ensure_wiki_repo()
    triggers_repo.purge_invalid_triggers(actor="system <system@agent-wiki>")
    triggers_repo.rebuild_from_filesystem()
    schedule_all_pending_cleanups()
    init_oauth(app)
    # Cross-process MCP pub-sub bridge: the worker process commits docs,
    # the web process owns the SSE stream — Postgres LISTEN/NOTIFY ferries
    # update events between them. In-process commits skip the round-trip
    # via the local fan-out in ``mcp_pubsub.publish_doc_update``; the
    # listener thread is for events that originate elsewhere.
    mcp_pubsub.start_listener()

    app.register_blueprint(auth.bp, url_prefix="/api/auth")
    app.register_blueprint(admin.bp, url_prefix="/api/admin")
    app.register_blueprint(users.bp, url_prefix="/api/users")
    app.register_blueprint(mcp_tokens.bp, url_prefix="/api/mcp/tokens")
    app.register_blueprint(mcp_connections.bp, url_prefix="/api/mcp/connections")
    app.register_blueprint(mcp_server.bp, url_prefix="/api/mcp")
    app.register_blueprint(documents.bp, url_prefix="/api/documents")
    app.register_blueprint(triggers.bp, url_prefix="/api/triggers")
    app.register_blueprint(events.bp, url_prefix="/api/events")
    app.register_blueprint(webhooks.bp, url_prefix="/api/webhooks")
    app.register_blueprint(chat.bp, url_prefix="/api/chat")
    app.register_blueprint(health.bp, url_prefix="/api/health")
    app.register_blueprint(llm.bp, url_prefix="/api/llm")
    app.register_blueprint(permissions.bp, url_prefix="/api")

    @app.errorhandler(PermissionDenied)
    def _permission_denied(err: PermissionDenied):  # type: ignore[unused-ignore]
        return jsonify(ErrorResponse(error=err.message).model_dump()), 403

    @app.errorhandler(QueueFullError)
    def _queue_full(err: QueueFullError):  # type: ignore[unused-ignore]
        return jsonify(QueueFullErrorResponse(
            error=str(err),
            queue=err.queue_name,
            size=err.size,
            limit=err.limit,
        ).model_dump()), 503

    @app.errorhandler(RequestError)
    def _request_error(err: RequestError):  # type: ignore[unused-ignore]
        return jsonify(ErrorResponse(error=err.message).model_dump()), err.status

    return app


def main() -> None:
    create_app().run(host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
