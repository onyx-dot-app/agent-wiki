"""Test-side helpers for the session cookie seam.

Mints a ``SessionMiddleware``-format cookie carrying ``user_id`` so
tests don't have to go through the full ``POST /api/auth/login`` flow
just to authenticate a test client. The signing format matches
Starlette's middleware (``itsdangerous.TimestampSigner`` over a
``b64(json(...))`` payload) so the cookie validates against the real
middleware installed in ``app.main``.
"""
from __future__ import annotations

import json
from base64 import b64encode

import itsdangerous
from fastapi.testclient import TestClient

import app.config as _app_config


def signed_session_cookie(user_id: str) -> str:
    """Build a Starlette-``SessionMiddleware``-compatible signed cookie
    payload for the given user."""
    # Read CONFIG via the module attribute at call time, not at import time.
    # ``tmp_config`` monkeypatches ``app.config.CONFIG``; a ``from ... import``
    # binding captures the original value (ci-test-secret in CI) and would
    # produce a key mismatch against the app's SessionMiddleware.
    signer = itsdangerous.TimestampSigner(str(_app_config.CONFIG.secret_key))
    data = b64encode(json.dumps({"user_id": user_id}).encode("utf-8"))
    return signer.sign(data).decode("utf-8")


def login_fastapi(client: TestClient, user_id: str) -> None:
    """Counterpart to the Flask ``client.session_transaction()`` login
    pattern. Sets the ``session`` cookie SessionMiddleware would write
    so subsequent ``client.get/post/...`` calls authenticate.

    Clears any existing session cookies first so there is exactly one
    session cookie in the jar — prevents stale server-set cookies from
    a prior signup from shadowing the new one.
    """
    client.cookies.delete("session")
    client.cookies.set("session", signed_session_cookie(user_id))
