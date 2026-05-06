"""Flask app entry. Wires up blueprints, then serves on 8080."""
from __future__ import annotations

from flask import Flask

from app.config import CONFIG
from app.db.sqlite import init_db
from app.api import auth, chat, documents, events, mcp, triggers, users, webhooks
from app.wiki.git import ensure_wiki_repo


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = CONFIG.secret_key

    init_db()
    ensure_wiki_repo()

    app.register_blueprint(auth.bp, url_prefix="/api/auth")
    app.register_blueprint(users.bp, url_prefix="/api/users")
    app.register_blueprint(mcp.bp, url_prefix="/api/mcp")
    app.register_blueprint(documents.bp, url_prefix="/api/documents")
    app.register_blueprint(triggers.bp, url_prefix="/api/triggers")
    app.register_blueprint(events.bp, url_prefix="/api/events")
    app.register_blueprint(webhooks.bp, url_prefix="/api/webhooks")
    app.register_blueprint(chat.bp, url_prefix="/api/chat")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    create_app().run(host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
