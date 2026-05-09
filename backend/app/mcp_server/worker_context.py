"""Worker-side helper that reconstitutes ``flask.g.user`` for a task.

The ``update_doc_nl`` worker (``app.tasks.document_update.agent_update_document_nl``)
runs in the documents-queue process — no Flask request, no logged-in
user. But every helper it transitively calls
(``commit_and_fan_out`` → ``require_can`` / agent-activity attribution
/ trigger ``actor`` field) reads the active user from ``flask.g.user``
via ``app.auth.current_user``.

This module bridges the two: pushing a minimal Flask app context for
the task duration and stuffing the bearer-resolved user into ``g.user``
gives the worker the same execution shape an HTTP request has —
without spinning up the full ``create_app``.

The chosen user is the *job's* user (``mcp_jobs.user_id``), looked
up at the start of the task. If the user has been deleted in the
meantime, the task fails fast with ``user_missing``.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

from flask import Flask, g

from app.auth import User
from app.auth import users as users_repo

log = logging.getLogger(__name__)

# Module-level singleton so we don't pay the Flask import + boot cost
# every task. Bare app — we only need the context shell, not blueprints.
# Lowercase name because pyright treats UPPER as a const that can't be
# rebound; the lazy-init pattern needs to write to it once.
_worker_app_singleton: Flask | None = None


def _worker_app() -> Flask:
    global _worker_app_singleton
    if _worker_app_singleton is None:
        app = Flask("mcp-worker")
        app.config["SECRET_KEY"] = "mcp-worker-context"
        _worker_app_singleton = app
    return _worker_app_singleton


@contextmanager
def as_user(user_id: str) -> Generator[User, None, None]:
    """Push a Flask app+request context and bind ``g.user`` to the
    User identified by ``user_id``.

    Yields the resolved ``User`` so the caller can reference its
    fields without re-querying. Raises ``UserMissingError`` if the
    user row no longer exists — the task should mark itself failed
    rather than silently degrade to anonymous.
    """
    row = users_repo.get_by_id(user_id)
    if row is None:
        raise UserMissingError(user_id)
    user = User(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        is_admin=bool(row["is_admin"]),
    )
    app = _worker_app()
    with app.test_request_context():
        g.user = user
        yield user


class UserMissingError(Exception):
    """The job's user_id no longer resolves to a row in ``users``."""

    def __init__(self, user_id: str) -> None:
        super().__init__(f"user {user_id!r} not found")
        self.user_id = user_id
