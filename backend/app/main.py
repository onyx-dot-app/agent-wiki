"""Flask app entry. Wires up blueprints, then serves on 8080."""
from __future__ import annotations

from datetime import timedelta

from flask import Flask, jsonify

from app.api import admin, auth, chat, documents, events, health, mcp, triggers, users, webhooks
from app.auth.oidc import init_oauth
from app.config import CONFIG
from app.db.sqlite import init_db
from app.tasks.agent_activity import schedule_all_pending_cleanups
from app.tasks.huey_app import QueueFullError
from app.triggers import repo as triggers_repo
from app.utils.logging import setup_logging
from app.wiki.git import ensure_wiki_repo
from app.wiki.search import bootstrap_index_if_empty


def create_app() -> Flask:
    setup_logging()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=CONFIG.secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=CONFIG.secure_cookies,
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    )

    init_db()
    ensure_wiki_repo()
    bootstrap_index_if_empty()
    triggers_repo.purge_invalid_triggers(actor="system <system@agent-wiki>")
    triggers_repo.rebuild_from_filesystem()
    schedule_all_pending_cleanups()
    init_oauth(app)

    app.register_blueprint(auth.bp, url_prefix="/api/auth")
    app.register_blueprint(admin.bp, url_prefix="/api/admin")
    app.register_blueprint(users.bp, url_prefix="/api/users")
    app.register_blueprint(mcp.bp, url_prefix="/api/mcp")
    app.register_blueprint(documents.bp, url_prefix="/api/documents")
    app.register_blueprint(triggers.bp, url_prefix="/api/triggers")
    app.register_blueprint(events.bp, url_prefix="/api/events")
    app.register_blueprint(webhooks.bp, url_prefix="/api/webhooks")
    app.register_blueprint(chat.bp, url_prefix="/api/chat")
    app.register_blueprint(health.bp, url_prefix="/api/health")

    @app.errorhandler(QueueFullError)
    def _queue_full(err: QueueFullError):  # type: ignore[unused-ignore]
        return jsonify(
            error=str(err),
            queue=err.queue_name,
            size=err.size,
            limit=err.limit,
        ), 503

    return app


def main() -> None:
    create_app().run(host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
